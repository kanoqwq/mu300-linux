"""Small device scripts, against a fake / (MU300_SYSROOT) and stub commands: mu300-device, mu300-lan-ip, mu300-led,
mu300-ttl. They run on Ubuntu (dash, bash) and OpenWrt (busybox ash)."""
import json
import os
import pty
import re
import select
import shlex
import shutil
import stat
import struct
import subprocess
import threading
import time
import unittest
import zlib
from pathlib import Path

from helpers import BIN, TOP, ShellTest


class Device(ShellTest):
    # the stock device trees as far as mu300-device looks: the F50's has neither node, the U30 Air's the charger
    # policy service, the V50's the AW9523B under I2C3 (its parent named soc:ap-ahb or ap-ahb, by kernel)
    V50_NODE = 'soc/{}/i2c@2270000/aw9523b_led@5b'

    def root(self, device=None, dt_u30=False, dt_v50=None, dt=False):
        r = self.tmp / 'root'
        shutil.rmtree(r / 'proc', ignore_errors=True)
        (r / 'run' / 'mu300').mkdir(parents=True, exist_ok=True)
        if device is not None:
            (r / 'run' / 'mu300' / 'device').write_text(device + '\n')
        if dt or dt_u30 or dt_v50:
            # every board's device tree has an I2C3 (the F50's is empty and disabled)
            (r / 'proc' / 'device-tree' / 'soc' / 'ap-ahb' / 'i2c@2270000').mkdir(parents=True, exist_ok=True)
        if dt_u30:
            (r / 'proc' / 'device-tree' / 'charger_policy_service').mkdir(parents=True, exist_ok=True)
        if dt_v50:
            (r / 'proc' / 'device-tree' / self.V50_NODE.format(dt_v50)).mkdir(parents=True, exist_ok=True)
        return r

    def test_mu300_device(self):
        cases = [(None, False, 'f50'), (None, True, 'u30air'), ('u30air', False, 'u30air'),
                 ('f50', True, 'f50')]  # what init wrote wins over the device tree
        for device, dt, want in cases:
            r = self.root(device, dt)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-device', MU300_SYSROOT=r).stdout.strip()
                self.assertEqual(out, want, (device, dt))
            (r / 'run' / 'mu300' / 'device').unlink(missing_ok=True)

    def test_mu300_device_three_boards(self):
        """The V50 (FINDINGS 95) is told by its AW9523B node whatever the label says; the F50 and the U30 Air answer
        exactly as before, with or without a label, and neither is ever taken for a V50 nor a V50 for either."""
        cases = [
            # an F50: its device tree, labelled or not, or labelled v50 by mistake (a backup moved over)
            (None, dict(dt=True), 'f50'), ('f50', dict(dt=True), 'f50'), ('v50', dict(dt=True), 'f50'),
            # a U30 Air: the charger policy service; a v50 label is not believed, and the AW9523B never makes a V50
            # of a U30 Air
            (None, dict(dt_u30=True), 'u30air'), ('u30air', dict(dt_u30=True), 'u30air'),
            ('v50', dict(dt_u30=True), 'u30air'), (None, dict(dt_u30=True, dt_v50='ap-ahb'), 'u30air'),
            ('u30air', dict(dt_v50='ap-ahb'), 'u30air'), ('f50', dict(dt_u30=True, dt_v50='ap-ahb'), 'f50'),
            # a V50: unlabelled (a mainline boot image of before), labelled v50, or labelled f50 by an older
            # installer; under either parent name
            (None, dict(dt_v50='ap-ahb'), 'v50'), (None, dict(dt_v50='soc:ap-ahb'), 'v50'),
            ('v50', dict(dt_v50='ap-ahb'), 'v50'), ('f50', dict(dt_v50='ap-ahb'), 'v50'),
            ('f50', dict(dt_v50='soc:ap-ahb'), 'v50'),
            # no device tree at all (not on a device): the label
            ('v50', {}, 'v50'), ('f50', {}, 'f50'), ('u30air', {}, 'u30air'), (None, {}, 'f50'),
        ]
        for device, dt, want in cases:
            r = self.root(device, **dt)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-device', MU300_SYSROOT=r).stdout.strip()
                self.assertEqual(out, want, (device, dt))
            (r / 'run' / 'mu300' / 'device').unlink(missing_ok=True)

    def test_device_rule_is_the_one_of_init(self):
        """boot/init and mu300-device decide alike: the v50 block is the same code in both."""
        init = (TOP / 'boot' / 'init').read_text()
        dev = (BIN / 'mu300-device').read_text()
        a = re.search(r'\nv50_dt\(\) \{.*?\n\}\nv50_fix\(\) \{.*?\n\}\n', init, re.S)
        b = re.search(r'\nv50_dt\(\) \{.*?\n\}\nv50_fix\(\) \{.*?\n\}\n', dev, re.S)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a.group(0), b.group(0))

    def test_json_for_programs(self):
        """mu300-device --json: the contract of docs/ADB-INTERFACE.md, one line a JSON parser takes."""
        r = self.root('u30air')
        (r / 'etc' / 'mu300').mkdir(parents=True)
        (r / 'etc' / 'mu300' / 'image-version').write_text('v2026.10.23\n')
        (r / 'etc' / 'os-release').write_text('NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\n')
        disk = self.tmp / 'disk'
        disk.mkdir()
        (disk / 'ubuntu').symlink_to(r)            # the directory of the Linux partition that is /
        self.stub('uname', 'echo 7.2.10')
        self.stub('id', 'echo 0')
        env = dict(MU300_SYSROOT=r, MU300_DISK=disk, MU300_BIN=BIN, MU300_ADB_CONF=r / 'etc/mu300/adb',
                   MU300_ADB_AUTH_CONF=r / 'etc/mu300/adb_auth', MU300_ADB_KEYS=r / 'etc/mu300/adb_keys',
                   MU300_ADB_ROOT_KEYS=self.tmp / 'none', MU300_ADB_OWNER=os.getuid(),
                   MU300_ADBD_BIN=shutil.which('true'), MU300_MODULES_BUILTIN=self.tmp / 'builtin')
        (self.tmp / 'builtin').write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')
        want = {'mu300': 1, 'device': 'u30air', 'system': 'ubuntu', 'version_id': '24.04',
                'release': 'v2026.10.23', 'kernel': '7.2.10', 'root': True, 'adb': 'open'}
        for shell in self.each_shell():
            out = self.script(shell, BIN / 'mu300-device', '--json', **env)
            self.assertEqual('', out.stderr)
            self.assertEqual(1, out.stdout.count('\n'))
            self.assertEqual(want, json.loads(out.stdout))
            # the order of the fields is not part of the contract, their names and kinds are
            self.assertEqual(list(want), list(json.loads(out.stdout)))
        # adb as the device has it set; not root; the copy kept by an update (ubuntu.old) is still "ubuntu"
        (r / 'etc/mu300/adb_auth').write_text('keys\n')
        (disk / 'ubuntu').unlink()
        (disk / 'ubuntu.old').symlink_to(r)
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            got = json.loads(self.script(shell, BIN / 'mu300-device', '--json', **env).stdout)
            self.assertEqual(('keys', False, 'ubuntu'), (got['adb'], got['root'], got['system']))
        (r / 'etc/mu300/adb').write_text('off\n')
        for shell in self.each_shell():
            got = json.loads(self.script(shell, BIN / 'mu300-device', '--json', **env).stdout)
            self.assertEqual('off', got['adb'])
        (r / 'etc/mu300/adb').unlink()
        # no adbd in this system
        for shell in self.each_shell():
            got = json.loads(self.script(shell, BIN / 'mu300-device', '--json',
                                         **dict(env, MU300_ADBD_BIN=self.tmp / 'none')).stdout)
            self.assertEqual('unavailable', got['adb'])
        # OpenWrt, with and without LuCI, where the Linux partition is not mounted; nothing in a value can end
        # the string it is in
        (disk / 'ubuntu.old').unlink()
        (r / 'etc' / 'openwrt_release').write_text("DISTRIB_ID='OpenWrt'\n")
        (r / 'etc' / 'os-release').write_text('NAME="OpenWrt"\nVERSION_ID="25.12.5"\n')
        (r / 'etc' / 'mu300' / 'image-version').write_text('v2026","root":true,"x":"\\\n')
        (r / 'run' / 'mu300' / 'device').write_text('f50\n')
        for shell in self.each_shell():
            got = json.loads(self.script(shell, BIN / 'mu300-device', '--json', **env).stdout)
            self.assertEqual(('f50', 'openwrt', '25.12.5', 'v2026roottruex'),
                             (got['device'], got['system'], got['version_id'], got['release']))
            self.assertFalse(got['root'])
        (r / 'www' / 'luci-static').mkdir(parents=True)
        (r / 'etc' / 'mu300' / 'image-version').unlink()
        for shell in self.each_shell():
            got = json.loads(self.script(shell, BIN / 'mu300-device', '--json', **env).stdout)
            self.assertEqual(('openwrt-luci', 'unknown'), (got['system'], got['release']))

    def test_lan_ip(self):
        for device, want in (('f50', '192.168.77.1'), ('u30air', '192.168.78.1'), ('', '192.168.77.1'),
                             ('v50', '192.168.79.1')):
            r = self.root(device)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-lan-ip', MU300_SYSROOT=r, MU300_BIN=BIN).stdout.strip()
                self.assertEqual(out, want, device)


class At(ShellTest):
    """mu300-at against a fake daemon directory: a FIFO `cmd` and a thread that answers through the answer file."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / 'at'
        self.dir.mkdir()
        os.mkfifo(self.dir / 'cmd')
        (self.tmp / 'bb').mkdir()
        self.sleeper = self.tmp / 'bb' / 'busybox'
        self.sleeper.write_text('#!/usr/bin/env python3\nimport sys, time\n'
                                'assert sys.argv[1] == "sleep"\ntime.sleep(float(sys.argv[2]))\n')
        self.sleeper.chmod(0o755)

    def daemon(self, reply, count=1, open_delay=0, answer_delay=0):
        """Answer COUNT commands with REPLY the way mu300-atd does: open the FIFO afresh for each command (after
        OPEN_DELAY: the daemon is busy draining), read `T answer-file command`, write the answer file after
        ANSWER_DELAY. A command whose writer has gone before the open is lost, as with the real daemon."""
        def run():
            for _ in range(count):
                time.sleep(open_delay)
                line = ''
                while not line:          # like the daemon: an empty read (no writer yet) just reopens
                    with open(self.dir / 'cmd') as f:
                        line = f.readline().rstrip('\n')
                _t, answer, _cmd = line.split(' ', 2)
                time.sleep(answer_delay)
                with open(answer, 'w', newline='') as a:
                    a.write(reply)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th

    def at(self, shell, *args):
        return self.script(shell, BIN / 'mu300-at', *args, MU300_AT_DIR=self.dir, MU300_BUSYBOX=self.sleeper)

    def test_answer_needs_a_final_result_code(self):
        for shell in self.each_shell():
            self.daemon('\r\n')
            r = self.at(shell, 'AT')
            self.assertEqual(r.returncode, 1)
            self.assertIn('modem returned no final response', r.stderr)
            self.daemon('+CSQ: 20,99\r\nOK\r\n')
            r = self.at(shell, 'AT+CSQ')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.replace('\r', ''), '+CSQ: 20,99\nOK\n')   # text mode folds CRLF

    def test_a_command_on_stdin(self):
        # "-": the command is the first line of stdin, for one that carries a secret (an APN password, FINDINGS 65)
        got = []

        def daemon():
            with open(self.dir / 'cmd') as f:
                line = f.readline().rstrip('\n')
            _t, answer, cmd = line.split(' ', 2)
            got.append(cmd)
            with open(answer, 'w', newline='') as a:
                a.write('OK\r\n')
        for shell in self.each_shell():
            del got[:]
            th = threading.Thread(target=daemon, daemon=True)
            th.start()
            r = self.script(shell, BIN / 'mu300-at', '-t', 2, '-', stdin='AT+CGPCO=0,"u","p w",1,3\nAT+CFUN=0\n',
                            MU300_AT_DIR=self.dir, MU300_BUSYBOX=self.sleeper)
            th.join(5)
            self.assertEqual((r.returncode, got), (0, ['AT+CGPCO=0,"u","p w",1,3']), r.stderr)   # one line, no more
            # nothing on stdin: the usage, and nothing sent
            r = self.script(shell, BIN / 'mu300-at', '-', stdin='', MU300_AT_DIR=self.dir, MU300_BUSYBOX=self.sleeper)
            self.assertEqual(r.returncode, 2)
            self.assertIn('usage', r.stderr)

    def test_no_daemon_does_not_hang(self):
        for shell in self.each_shell():
            t0 = time.monotonic()
            r = self.at(shell, '-t', 1, 'AT')
            self.assertLess(time.monotonic() - t0, 3 + 0.5)
            self.assertEqual(r.returncode, 1)
            self.assertIn('no answer from the daemon', r.stderr)
            self.assertFalse((self.dir / 'lock').exists())

    def test_dead_owner_says_only_no_answer(self):
        # owner/pid naming a process that is gone (the daemon died, or the modem never came up): the client says
        # "no answer" and nothing else - mobile-data copies its stderr into radio.log
        (self.dir / 'owner').mkdir()
        (self.dir / 'owner' / 'pid').write_text('999999\n')
        for shell in self.each_shell():
            r = self.at(shell, '-t', 1, 'AT')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stderr.strip(), 'mu300-at: no answer from the daemon', shell)

    def test_command_survives_a_busy_daemon(self):
        # the daemon opens the FIFO only 0.5 s after the client wrote: the command must still be there
        for shell in self.each_shell():
            self.daemon('OK\r\n', open_delay=0.5)
            r = self.at(shell, '-t', 2, 'AT')
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_live_daemon_gets_a_long_budget(self):
        # a live mu300-atd (owner/pid) may drain before it collects: an answer after T + 2 s is still taken
        owner = subprocess.Popen(['sh', '-c', 'sleep 60; :', 'mu300-atd'])
        try:
            (self.dir / 'owner').mkdir()
            (self.dir / 'owner' / 'pid').write_text(f'{owner.pid}\n')
            if not Path(f'/proc/{owner.pid}/cmdline').exists():
                self.skipTest('no /proc')
            for shell in self.each_shell():
                self.daemon('OK\r\n', answer_delay=3.5)
                r = self.at(shell, '-t', 1, 'AT')
                self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            owner.kill()
            owner.wait()

    def test_a_caller_budget_caps_the_wait(self):
        # final review minor 4: a caller with a hard limit of its own (the LuCI panel; rpcd ends a call at 30 s)
        # caps the wait with MU300_AT_BUDGET, even for a live daemon; it never lengthens it, and junk is ignored
        owner = subprocess.Popen(['sh', '-c', 'sleep 60; :', 'mu300-atd'])
        try:
            (self.dir / 'owner').mkdir()
            (self.dir / 'owner' / 'pid').write_text(f'{owner.pid}\n')
            if not Path(f'/proc/{owner.pid}/cmdline').exists():
                self.skipTest('no /proc')
            for shell in self.each_shell():
                for budget, ok in (('2', False), ('x', True), ('60', True)):
                    with self.subTest(budget=budget):
                        th = self.daemon('OK\r\n', answer_delay=3.5)
                        t0 = time.monotonic()
                        r = self.script(shell, BIN / 'mu300-at', '-t', 1, 'AT', MU300_AT_DIR=self.dir,
                                        MU300_BUSYBOX=self.sleeper, MU300_AT_BUDGET=budget)
                        self.assertEqual(r.returncode == 0, ok, r.stderr)
                        if not ok:
                            self.assertLess(time.monotonic() - t0, 3)
                            self.assertIn('no answer from the daemon', r.stderr)
                        th.join(timeout=10)
                        for f in self.dir.glob('answer.*'):
                            f.unlink()
        finally:
            owner.kill()
            owner.wait()

    def test_signal_stops_the_client_and_frees_the_lock(self):
        for shell in self.each_shell():
            p = subprocess.Popen(shell + [str(BIN / 'mu300-at'), '-t', '20', 'AT'], stderr=subprocess.PIPE,
                                 env=self.env(MU300_AT_DIR=self.dir, MU300_BUSYBOX=self.sleeper))
            for _ in range(200):
                if (self.dir / 'lock').exists():
                    break
                time.sleep(0.02)
            time.sleep(0.3)
            p.terminate()
            self.assertEqual(p.wait(timeout=10), 143)
            p.stderr.close()
            self.assertFalse((self.dir / 'lock').exists())
            self.assertEqual(list(self.dir.glob('answer.*')), [])

    def test_vanished_fifo_is_not_replaced_by_a_file(self):
        for shell in self.each_shell():
            (self.dir / 'cmd').unlink()
            r = self.at(shell, 'AT')           # no FIFO: the direct path, which has no tty here
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((self.dir / 'cmd').exists())
            os.mkfifo(self.dir / 'cmd')

    def test_fast_round_trip(self):
        for shell in self.each_shell():
            self.daemon('OK\r\n', 20)
            t0 = time.monotonic()
            for _ in range(20):
                self.assertEqual(self.at(shell, 'AT').returncode, 0)
            self.assertLess(time.monotonic() - t0, 3)

    def test_timeout_is_whole_seconds(self):
        """kanoqwq 25f8d0c: -t goes into arithmetic and into the request line. Only what mu300-atd parses (1-99) is
        accepted: a wider range made "120 /run/... AT" a malformed line the daemon sent to the modem whole."""
        for shell in self.each_shell():
            for bad in (['-t'], ['-t', '0', 'AT'], ['-t', 'x', 'AT'], ['-t', '1.5', 'AT'], ['-t', '1 2', 'AT'],
                        ['-t', '100', 'AT'], ['-t', '07', 'AT']):
                with self.subTest(args=bad):
                    r = self.at(shell, *bad)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertFalse((self.dir / 'lock').exists())

    REFUSED = ('AT\n1 {tmp}/owned AT+CFUN=0',      # a second fifo request with an answer file of its own
               'AT+CSQ\rAT+CFUN=0',                # a second modem command
               'AT+CFUN=0\r',
               'AT+CMGS=12\r0011',                 # a PDU send without its Ctrl-Z
               'AT+CMGS=12\r00\rAT+CFUN=0\x1a',
               'AT+CMGS=12\r00 11\x1a',
               'AT+CMGS=12\r\x1a',
               'AT+CMGS=1x\r0011\x1a',
               'AT+CMGS=12\r0011\x1a\n')
    ACCEPTED = ('AT', 'AT+CSQ', 'AT+CMGS=12\r0011AbCd\x1a', 'AT+CMGW=7\r00\x1a', '0011\x1a', 'AT+X="a b"')

    def test_one_request_per_call(self):
        """kanoqwq 25f8d0c, tightened: a line feed anywhere and a CR anywhere but in AT+CMGS=N<CR>PDU<Ctrl-Z> are
        refused before anything is written, on the daemon path and the direct path alike."""
        for shell in self.each_shell():
            for cmd in self.REFUSED:
                cmd = cmd.format(tmp=self.tmp)
                with self.subTest(shell=shell, cmd=cmd):
                    r = self.at(shell, '-t', 1, cmd)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn('one command per call', r.stderr)
                    self.assertFalse((self.dir / 'lock').exists())
                    self.assertFalse((self.tmp / 'owned').exists())
            self.daemon('> \r\n+CMGS: 7\r\nOK\r\n')
            r = self.at(shell, '-t', 2, 'AT+CMGS=12\r0011\x1a')
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_client_and_daemon_share_one_framing_rule(self):
        """The one_request test is the same function in mu300-at and mu300-atd, and it gives the expected verdicts."""
        def body(path):
            m = re.search(r'^one_request\(\) \{.*?\n\}\n', path.read_text(), re.S | re.M)
            self.assertIsNotNone(m, path)
            return re.sub(r'  #.*', '', m.group(0))      # the comment on the first line differs
        client, daemon = body(BIN / 'mu300-at'), body(BIN / 'mu300-atd')
        self.assertEqual(client, daemon)
        prelude = "CR=$(printf '\\r'); LF='\n'\nSUB=$(printf '\\032')\n" + client
        for shell in self.each_shell():
            for cmd, want in [(c, 1) for c in self.REFUSED] + [(c, 0) for c in self.ACCEPTED]:
                cmd = cmd.format(tmp=self.tmp)
                with self.subTest(shell=shell, cmd=cmd):
                    r = subprocess.run(shell + ['-c', prelude + 'one_request "$1"', 'sh', cmd])
                    self.assertEqual(r.returncode, want)

    def test_lock_wait_is_in_seconds(self):
        """kanoqwq 25f8d0c: the lock wait counted rounds of 0.1 s; with no sleep that takes a fraction each round was
        a whole second, and MU300_AT_LOCK_WAIT=2 waited 20 s. Now it is 2 s of the clock."""
        holder = subprocess.Popen(['sleep', '60'])
        try:
            self.stub('sleep', 'case $1 in *.*) exit 1 ;; esac\nexec /bin/sleep "$@"')
            for shell in self.each_shell():
                (self.dir / 'lock').mkdir(exist_ok=True)
                (self.dir / 'lock' / 'pid').write_text(f'{holder.pid}\n')
                t0 = time.monotonic()
                r = self.script(shell, BIN / 'mu300-at', '-t', 1, 'AT', MU300_AT_DIR=self.dir,
                                MU300_BUSYBOX=self.tmp / 'none', MU300_AT_LOCK_WAIT=2)
                self.assertLess(time.monotonic() - t0, 5)
                self.assertEqual(r.returncode, 1)
                self.assertIn('busy', r.stderr)
        finally:
            holder.kill()
            holder.wait()

    def test_direct_path_needs_a_final_result_code(self):
        """kanoqwq 25f8d0c: with no daemon the client opens the tty itself; silence there ended with status 0."""
        master, slave = pty.openpty()
        try:
            (self.dir / 'cmd').unlink()
            dev = os.ttyname(slave)
            for shell in self.each_shell():
                r = self.script(shell, BIN / 'mu300-at', '-t', 1, 'AT', MU300_AT_DIR=self.dir, MU300_AT_DEV=dev)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('no final response', r.stderr)
                while select.select([master], [], [], 0.2)[0]:   # what the silent run sent, before the next one
                    os.read(master, 256)

                def modem():
                    buf = b''
                    while b'AT\r' not in buf:
                        if not select.select([master], [], [], 10)[0]:
                            return
                        buf += os.read(master, 256)
                    os.write(master, b'\r\nOK\r\n')
                th = threading.Thread(target=modem, daemon=True)
                th.start()
                r = self.script(shell, BIN / 'mu300-at', '-t', 2, 'AT', MU300_AT_DIR=self.dir, MU300_AT_DEV=dev)
                th.join(timeout=10)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('OK', r.stdout)
        finally:
            os.close(master)
            os.close(slave)


class Atd(ShellTest):
    """mu300-atd on a pseudo terminal pair: the test is the modem (the pty master), the daemon runs for real."""

    FAST = 0.95   # a round trip without the one second drain wait: read -t 1 alone cannot be faster than this

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / 'at'
        self.procs = []
        self.masters = []
        self.slave_fds = []

    def tearDown(self):
        for p in self.procs:
            p.terminate()                 # the daemon's INT/TERM trap stops its drainers
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for fd in self.masters + self.slave_fds:
            os.close(fd)
        super().tearDown()

    def pty(self, name):
        """A character device RUN/NAME (a symlink to a pty slave) and the master fd behind it."""
        master, slave = pty.openpty()
        self.masters.append(master)
        self.slave_fds.append(slave)      # keep the slave open: no hangup
        link = self.run_dir / name
        link.symlink_to(os.ttyname(slave))
        return link, master

    def start(self, shell, nr0=True, **env):
        """Start the daemon on tty_nr1 (and a tty_nr0 if NR0); wait until it owns the channel and every drainer it
        started has made its log. Returns the URC logs and the nr1 master."""
        self.run_dir = self.tmp / f'run{len(self.procs)}'   # one per daemon: each_shell starts several
        self.run_dir.mkdir(exist_ok=True)
        self.dir = self.run_dir / 'at'
        dev, master = self.pty('tty_nr1')
        if nr0:
            self.pty('tty_nr0')
        e = self.env(MU300_AT_DEV=dev, MU300_AT_DIR=self.dir, **env)
        if 'MU300_AT_URC_CHANNELS' not in env:
            e.pop('MU300_AT_URC_CHANNELS', None)
        self.err = self.run_dir / 'atd.err'
        with self.err.open('w') as err:
            self.procs.append(subprocess.Popen(shell + [str(BIN / 'mu300-atd')], stderr=err, env=e))
        # "... is ours, draining N other channel(s)" comes once every drainer has been started
        n = None
        for _ in range(500):
            m = re.search(r'is ours, draining\s+(\d+) other', self.err.read_text())
            if m:
                n = int(m.group(1))
                break
            time.sleep(0.02)
        self.assertIsNotNone(n, 'the daemon did not come up: ' + self.err.read_text())
        urc = self.dir / 'urc'
        for _ in range(500):              # a drainer makes its log in the background
            logs = sorted(p.name for p in urc.iterdir())
            if len(logs) >= n:
                break
            time.sleep(0.02)
        return logs, master

    def send(self, master, cmd, reply, t=1, before_reply=None):
        """Hand the daemon CMD (as a client does), answer on the pty when it arrives; seconds until the answer file.
        The answer itself is left in self.answer. BEFORE_REPLY, if given, runs between the two."""
        answer = self.dir / 'answer.4242'      # the daemon writes only $DIR/answer.<pid>
        answer.unlink(missing_ok=True)
        t0 = time.monotonic()
        with open(self.dir / 'cmd', 'w') as f:
            f.write(f'{t} {answer} {cmd}\n')
        buf = b''
        while cmd.encode() + b'\r' not in buf:
            if not select.select([master], [], [], 10)[0]:
                break
            buf += os.read(master, 256)
        if before_reply:
            before_reply()
        if reply is not None:
            os.write(master, reply)
        while not answer.exists() and time.monotonic() - t0 < 40:
            time.sleep(0.02)
        elapsed = time.monotonic() - t0
        self.answer = answer.read_text() if answer.exists() else None
        return elapsed

    def test_a_command_with_a_secret_leaves_nothing_readable_behind(self):
        """FINDINGS 65: an APN password travels in AT+CGPCO. The answer file is root's alone (a modem that echoes the
        command puts it there, and the file of a client that gave up stays), and an echo that comes late is thrown
        away, never appended to the URC log."""
        cmd = 'AT+CGPCO=0,"user","hunter2",1,3'
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            self.send(m, cmd, (cmd + '\r\nOK\r\n').encode())
            self.assertEqual(self.answer, cmd + '\nOK\n')
            self.assertEqual((self.dir / 'answer.4242').stat().st_mode & 0o777, 0o600)
            # no answer in time; the echo and the OK arrive late, and the next command's drain finds them
            self.send(m, cmd, None, t=1)
            os.write(m, (cmd + '\r\n+CGEV: late\r\nOK\r\n').encode())
            time.sleep(0.3)
            self.send(m, 'AT', b'OK\r\n')
            self.assertEqual(self.answer, 'OK\n')
            log = (self.dir / 'urc' / 'tty_nr1.log').read_text()
            self.assertIn('+CGEV: late', log)                     # what is no echo is still kept
            self.assertNotIn('hunter2', log)
            self.assertNotIn('CGPCO', log)
            for p in self.dir.rglob('*'):
                if p.is_file() and p.name != 'answer.4242':
                    self.assertNotIn(b'hunter2', p.read_bytes(), p)
            self.assertNotIn('hunter2', self.err.read_text())

    def test_requests_are_parsed_strictly(self):
        """A malformed request line is dropped, never sent to the modem whole (it used to be: "120 /run/... AT" with
        a timeout the daemon did not take), and an answer file outside $DIR is never written."""
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            outside = self.tmp / 'outside'
            for line in (f'120 {self.dir}/answer.1 AT+BAD', f'6 {outside} AT+BAD', f'6 {self.dir}/answer.1x AT+BAD',
                         f'6 {self.dir}/../answer.1 AT+BAD', 'AT+BAD', f'6 {self.dir}/answer.1'):
                # read-write like mu300-at: the lines stay in the fifo while the daemon reopens it once per line
                fd = os.open(self.dir / 'cmd', os.O_RDWR)
                os.write(fd, (line + '\n').encode())
                for _ in range(250):              # wait until the daemon has taken it (and dropped it)
                    if not select.select([fd], [], [], 0)[0]:
                        break
                    time.sleep(0.02)
                time.sleep(0.2)
                os.close(fd)
            self.assertFalse(select.select([m], [], [], 0.3)[0])     # none of it reached the channel
            self.send(m, 'AT', b'\r\nOK\r\n')      # served after the dropped ones: the daemon is still reading
            self.assertEqual(self.answer, 'OK\n')
            self.assertFalse(outside.exists())
            self.assertFalse((self.dir / 'answer.1').exists())
            self.assertIn('malformed request', self.err.read_text())

    def test_the_urc_logs_are_roots_alone(self):
        """What the modem says unasked is the owner's to read and nobody else's: with caller id enabled (an AT client
        watch) every ring puts the caller's number into this log."""
        for shell in self.shells:
            logs, m = self.start(shell)
            self.assertEqual(logs, ['tty_nr0.log'])
            self.assertEqual((self.dir / 'urc').stat().st_mode & 0o777, 0o700)

    def test_a_directory_somebody_else_made_first_is_replaced(self):
        """/run takes files from every user: a symlink (or a foreign directory) where $DIR should be is not where the
        command fifo and the answers go."""
        lib = BIN.parent / 'lib' / 'run-dir.sh'
        for shell in self.shells:
            elsewhere = self.tmp / f'elsewhere{len(self.procs)}'
            elsewhere.mkdir()
            run = self.tmp / f'run{len(self.procs)}'
            run.mkdir()
            (run / 'at').symlink_to(elsewhere)
            logs, m = self.start(shell, MU300_RUN_DIR_LIB=lib)
            self.assertFalse(self.dir.is_symlink())
            self.assertEqual(self.dir.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(elsewhere.iterdir()), [])
            self.assertIn("was somebody else's", self.err.read_text())
            self.send(m, 'AT', b'\r\nOK\r\n')
            self.assertEqual(self.answer, 'OK\n')

    def test_a_second_command_never_reaches_the_modem(self):
        """The daemon repeats mu300-at's one_request test: a CR outside AT+CMGS=N<CR>PDU<Ctrl-Z> is refused with an
        answer that has no final result code (so the client fails), and the modem never sees it."""
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            answer = self.dir / 'answer.7'
            fd = os.open(self.dir / 'cmd', os.O_RDWR)
            os.write(fd, f'1 {answer} AT+CSQ\rAT+CFUN=0\n'.encode())
            for _ in range(250):
                if answer.exists():
                    break
                time.sleep(0.02)
            os.close(fd)
            self.assertEqual(answer.read_text(), 'mu300-atd: refused\n')
            self.assertFalse(select.select([m], [], [], 0.3)[0])     # nothing was written to the channel
            self.send(m, 'AT+CMGS=3\r0011\x1a', b'\r\n> \r\n+CMGS: 9\r\nOK\r\n', t=2)
            self.assertIn('+CMGS: 9', self.answer)

    def whole_seconds(self):
        """The daemon's shell reads in whole seconds only (bash 3.2): every drain then waits the full second."""
        return 'takes whole seconds' in self.err.read_text()

    def test_log_lines_after_open_reach_stderr(self):
        for shell in self.each_shell():
            self.start(shell)
            self.assertIn('is ours, draining', self.err.read_text())

    def test_unset_urc_channels_open_nr0(self):
        for shell in self.each_shell():
            logs, _m = self.start(shell)
            self.assertEqual(logs, ['tty_nr0.log'])

    # ------------------------------------------------------------------ a channel that is not ready yet (#226)
    # A SIPC tty exists before the modem has started, and opening it fails (ENODEV) until then. Here the tty is a
    # link to /dev/tty, which a process without a controlling terminal cannot open (ENXIO): a character device whose
    # open fails, as the modem's does at a cold boot. The link is then turned to a pty: the channel became ready.
    def start_unready(self, shell, wait):
        self.run_dir = self.tmp / f'run{len(self.procs)}'
        self.run_dir.mkdir()
        self.dir = self.run_dir / 'at'
        dev = self.run_dir / 'tty_nr1'
        dev.symlink_to('/dev/tty')
        e = self.env(MU300_AT_DEV=dev, MU300_AT_DIR=self.dir, MU300_AT_URC_CHANNELS='', MU300_AT_OPEN_WAIT=wait)
        self.err = self.run_dir / 'atd.err'
        with self.err.open('w') as err:
            # a session of its own: no controlling terminal, so /dev/tty does not open
            p = subprocess.Popen(shell + [str(BIN / 'mu300-atd')], stderr=err, env=e, start_new_session=True)
        self.procs.append(p)
        return dev, p

    def wait_for(self, text, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if text in self.err.read_text():
                return True
            time.sleep(0.05)
        return False

    def test_a_channel_not_ready_yet_is_waited_for(self):
        """#226: the first open failed, and with it the shell (a failed `exec` redirection ends dash and ash), so
        the daemon was gone after one line. Now it waits for the channel, holding the command fifo meanwhile."""
        for shell in self.each_shell():
            dev, p = self.start_unready(shell, 60)
            self.assertTrue(self.wait_for('is not ready'), self.err.read_text())
            self.assertIsNone(p.poll())                              # still there, waiting
            self.assertTrue((self.dir / 'cmd').exists())             # clients queue at the daemon, not at the tty
            master, slave = pty.openpty()
            self.masters.append(master)
            self.slave_fds.append(slave)
            tmp = self.run_dir / 'tty_nr1.new'
            tmp.symlink_to(os.ttyname(slave))
            os.replace(tmp, dev)                                     # the modem is up
            self.assertTrue(self.wait_for('is ours, draining'), self.err.read_text())
            self.assertRegex(self.err.read_text(), r'ready after \d+ s')
            self.send(master, 'AT', b'\r\nOK\r\n')
            self.assertEqual(self.answer, 'OK\n')
            self.assertNotIn("can't create", self.err.read_text())

    def test_a_channel_that_never_gets_ready_ends_the_wait(self):
        """The modem never starts (#226's unit: no eMMC, so no modem image): after MU300_AT_OPEN_WAIT seconds one
        line, exit 1 for the supervisor to start it again, and neither the fifo nor the owner left behind."""
        for shell in self.each_shell():
            _dev, p = self.start_unready(shell, 2)
            self.assertEqual(p.wait(timeout=30), 1)
            err = self.err.read_text()
            self.assertEqual(err.count('is not ready'), 1, err)
            self.assertIn('did not become ready in 2 s', err)
            self.assertFalse((self.dir / 'cmd').exists())
            self.assertFalse((self.dir / 'owner').exists())

    def test_every_open_survives_its_failure(self):
        # the bare form is gone: `exec 3<>"$DEV" || return 1` never reached its return in dash and ash
        src = (BIN / 'mu300-atd').read_text()
        self.assertIn('command exec 3<>"$DEV" || return 1', src)
        self.assertNotRegex(src, r'(?m)^\s*exec 3<>')

    # ------------------------------------------------------------------ +CMT frames (direct SMS delivery)
    PDU = '07911326040000F0040B911346610089F60000208062917314480CC8F71D14969741F977FD07'
    SPOOLER = BIN / 'mu300-cmt-spool'

    def start_spooling(self, shell, nr0=True):
        """The daemon with the real spooler and a pool of its own; returns (nr1 master, nr0 master or None)."""
        self.pool = self.tmp / f'pool{len(self.procs)}'
        _logs, m = self.start(shell, nr0=nr0, MU300_SMS_POOL=self.pool, MU300_CMT_SPOOL_BIN=self.SPOOLER)
        return m, (self.masters[-1] if nr0 else None)

    def spooled(self, timeout=10):
        """[the PDUs in the pool's spool], once one is there (or the time is up)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            d = self.pool / 'incoming'
            files = sorted(d.glob('*.ready')) if d.exists() else []
            if files:
                return [f.read_text().rstrip('\n') for f in files]
            time.sleep(0.05)
        return []

    def test_a_cmt_frame_on_the_urc_channel_is_spooled_and_kept_out_of_the_log(self):
        """The modem's unsolicited channel (nr0, where +CMTI comes) carries the +CMT frame when mu300-smsd asked for
        direct delivery: the PDU goes to the spooler on its stdin, the log gets the header and not the message."""
        for shell in self.each_shell():
            _m, nr0 = self.start_spooling(shell)
            os.write(nr0, f'\r\n+CSQ: 20,99\r\n\r\n+CMT: ,27\r\n{self.PDU}\r\n\r\n+CEREG: 1\r\n'.encode())
            self.assertEqual(self.spooled(), [self.PDU])
            log = self.dir / 'urc' / 'tty_nr0.log'
            for _ in range(100):
                if '+CEREG: 1' in log.read_text():
                    break
                time.sleep(0.05)
            self.assertEqual([l for l in log.read_text().splitlines() if l], ['+CSQ: 20,99', '+CMT (PDU spooled)', '+CEREG: 1'])
            self.assertNotIn(self.PDU, log.read_text())
            self.assertNotIn(self.PDU, self.err.read_text())

    def test_a_cmt_frame_inside_a_reply_is_spooled_and_never_in_the_answer(self):
        for shell in self.each_shell():
            m, _nr0 = self.start_spooling(shell, nr0=False)
            self.send(m, 'AT+CSQ', f'\r\n+CMT: ,27\r\n{self.PDU}\r\n\r\n+CSQ: 20,99\r\n\r\nOK\r\n'.encode(), t=3)
            self.assertEqual(self.answer, '+CSQ: 20,99\nOK\n')
            self.assertEqual(self.spooled(), [self.PDU])
            self.assertIn('+CMT (PDU spooled)\n', (self.dir / 'urc' / 'tty_nr1.log').read_text())
            self.assertNotIn(self.PDU, (self.dir / 'urc' / 'tty_nr1.log').read_text())

    def test_a_cmt_frame_while_idle_is_spooled_before_the_next_command(self):
        for shell in self.each_shell():
            m, _nr0 = self.start_spooling(shell, nr0=False)
            time.sleep(1.3)                                          # past the daemon's startup drain (a quiet second)
            os.write(m, f'\r\n+CMT: "ADANA BLD",27\r\n{self.PDU}\r\n'.encode())
            time.sleep(0.3)
            self.assertEqual(self.spooled(timeout=0.1), [])        # nobody reads an idle command channel
            self.send(m, 'AT', b'\r\nOK\r\n', t=3)                 # the drain before the command takes it
            self.assertEqual(self.answer, 'OK\n')
            self.assertEqual(self.spooled(), [self.PDU])
            self.assertIn('+CMT (PDU spooled)\n', (self.dir / 'urc' / 'tty_nr1.log').read_text())
            self.assertNotIn('ADANA', (self.dir / 'urc' / 'tty_nr1.log').read_text())   # the header is never logged

    def test_a_frame_that_is_no_message_is_not_spooled_and_the_daemon_goes_on(self):
        for shell in self.each_shell():
            _m, nr0 = self.start_spooling(shell)
            os.write(nr0, b'\r\n+CMT: ,27\r\nnot a pdu\r\n+CSQ: 1,2\r\n')
            log = self.dir / 'urc' / 'tty_nr0.log'
            for _ in range(100):
                if '+CSQ: 1,2' in log.read_text():
                    break
                time.sleep(0.05)
            self.assertEqual([l for l in log.read_text().splitlines() if l], ['+CMT (not spooled)', '+CSQ: 1,2'])
            self.assertEqual(self.spooled(timeout=0.2), [])
            self.assertIn('could not be spooled', self.err.read_text())
            # a header with no PDU after it (two seconds): the same, and the lines after it are logged as usual
            os.write(nr0, b'+CMT: ,27\r\n')
            time.sleep(2.5)
            os.write(nr0, b'+CSQ: 3,4\r\n')
            for _ in range(100):
                if '+CSQ: 3,4' in log.read_text():
                    break
                time.sleep(0.05)
            self.assertEqual([l for l in log.read_text().splitlines() if l][-2:], ['+CMT (not spooled)', '+CSQ: 3,4'])
            self.assertEqual(self.spooled(timeout=0.2), [])

    def test_a_text_mode_frame_is_written_nowhere(self):
        """Nothing here asks for text mode, but another tool might: then the header carries the sender and the next
        line the message. Neither reaches the log, a reply or stderr; the spooler takes hexadecimal only."""
        header, text = '+CMT: "+905551112233",,"26/10/12,15:00:00+12"', 'the secret text of a message'
        for shell in self.each_shell():
            m, nr0 = self.start_spooling(shell)
            os.write(nr0, f'\r\n{header}\r\n{text}\r\n+CSQ: 1,2\r\n'.encode())
            log0 = self.dir / 'urc' / 'tty_nr0.log'
            for _ in range(100):
                if '+CSQ: 1,2' in log0.read_text():
                    break
                time.sleep(0.05)
            self.assertEqual([l for l in log0.read_text().splitlines() if l], ['+CMT (not spooled)', '+CSQ: 1,2'])
            # and inside a reply on the command channel
            self.send(m, 'AT+CSQ', f'\r\n{header}\r\n{text}\r\n\r\n+CSQ: 20,99\r\n\r\nOK\r\n'.encode(), t=3)
            self.assertEqual(self.answer, '+CSQ: 20,99\nOK\n')
            everything = log0.read_text() + (self.dir / 'urc' / 'tty_nr1.log').read_text() + self.err.read_text()
            for secret in ('905551112233', 'secret', '26/10/12'):
                self.assertNotIn(secret, everything)
            self.assertEqual(self.spooled(timeout=0.2), [])

    def test_without_the_spooler_nothing_is_spooled_and_nothing_breaks(self):
        for shell in self.each_shell():
            self.pool = self.tmp / f'pool{len(self.procs)}'
            _logs, m = self.start(shell, nr0=False, MU300_SMS_POOL=self.pool, MU300_CMT_SPOOL_BIN=self.tmp / 'nowhere')
            self.send(m, 'AT+CSQ', f'\r\n+CMT: ,27\r\n{self.PDU}\r\n\r\n+CSQ: 20,99\r\n\r\nOK\r\n'.encode(), t=3)
            self.assertEqual(self.answer, '+CSQ: 20,99\nOK\n')
            self.assertFalse((self.pool / 'incoming').exists())
            self.assertIn('+CMT (not spooled)\n', (self.dir / 'urc' / 'tty_nr1.log').read_text())

    def test_empty_urc_channels_open_none(self):
        for shell in self.each_shell():
            logs, _m = self.start(shell, MU300_AT_URC_CHANNELS='')
            self.assertRegex(self.err.read_text(), r'draining\s+0 other')
            self.assertEqual(logs, [])

    def test_no_drain_wait_after_a_clean_reply(self):
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            self.send(m, 'AT', b'\r\nOK\r\n')                 # the first command drains slowly: nothing known yet
            if self.whole_seconds():
                continue
            for reply in (b'\r\nOK\r\n', b'\r\nERROR\r\n', b'\r\nOK\r\n'):   # the last one times the drain after ERROR
                self.assertLess(self.send(m, 'AT', reply), self.FAST)

    def test_slow_drain_after_a_timeout(self):
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            self.send(m, 'AT', b'\r\nOK\r\n')
            self.send(m, 'AT+X', None, t=1)                      # no answer: the timeout path
            self.assertGreater(self.send(m, 'AT', b'\r\nOK\r\n'), 0.9)
            if not self.whole_seconds():
                self.assertLess(self.send(m, 'AT', b'\r\nOK\r\n'), self.FAST)   # and a clean one makes it fast again

    def test_stray_lines_while_idle_do_not_shift_the_answers(self):
        # A line that arrives after a clean reply and before the next command - a late final code, a URC, an
        # unsolicited NO CARRIER - is drained into the nr1 log, never taken as the next command's answer.
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            self.send(m, 'AT', b'\r\nOK\r\n')
            # (a) a late trailing final code, the moment the reply that ended in OK has been handed over
            self.send(m, 'AT+A', b'\r\n+A: 1\r\nOK\r\n')
            self.assertEqual(self.answer, '+A: 1\nOK\n')
            os.write(m, b'\r\nOK\r\n')
            self.send(m, 'AT+B', b'\r\n+B: 2\r\nOK\r\n')
            self.assertEqual(self.answer, '+B: 2\nOK\n')
            # (b) URCs and an unsolicited final code while the channel is idle, before the next command
            os.write(m, b'\r\n+CMTI: "SM",3\r\n\r\nNO CARRIER\r\n')
            time.sleep(0.2)
            self.send(m, 'AT+C', b'\r\n+C: 3\r\nOK\r\n')
            self.assertEqual(self.answer, '+C: 3\nOK\n')
            self.send(m, 'AT+D', b'\r\nERROR\r\n')
            self.assertEqual(self.answer, 'ERROR\n')
            self.assertEqual((self.dir / 'urc' / 'tty_nr1.log').read_text().split('\n'),
                             ['OK', '+CMTI: "SM",3', 'NO CARRIER', ''])

    def test_a_reply_after_a_second_boundary_is_still_taken(self):
        # The reply budget counts whole seconds: "now + T" used to end at the next second boundary, so a T=1
        # command answered just after it came back empty, even though the modem took 0.1 s at most.
        def past_the_boundary():
            frac = time.time() % 1
            if frac > 0.1:       # else a boundary may lie between the daemon's clock read and now: no test this time
                time.sleep(1 - frac + 0.05)
        for shell in self.each_shell():
            _l, m = self.start(shell, nr0=False)
            for _ in range(3):
                self.send(m, 'AT+E', b'\r\n+E: 5\r\nOK\r\n', before_reply=past_the_boundary)
                self.assertEqual(self.answer, '+E: 5\nOK\n')


class MobileData(ShellTest):
    """mobile-data's at(): which daemon directory the command goes to (K17). The function is cut out of the script and
    its absolute paths pointed at the scratch directory, so nothing else of mobile-data runs."""

    def run_at(self, shell, nr2, nr1=True):
        run = self.tmp / 'run'
        shutil.rmtree(run, ignore_errors=True)
        for flag, name in ((nr1, 'mu300-at'), (nr2, 'mu300-at2')):
            d = run / name
            d.mkdir(parents=True, exist_ok=True)
            if flag:
                os.mkfifo(d / 'cmd')
        self.stub('mu300-at', 'echo "dir=$MU300_AT_DIR args=$*" >> "$STUBLOG/calls"')
        text = (BIN / 'mobile-data').read_text()
        body = text[text.index('\nat() {'):text.index('\n}\n', text.index('\nat() {')) + 3]
        body = body.replace('/run/', f'{run}/').replace('/opt/mu300/bin/mu300-at', 'mu300-at')
        (self.tmp / 'calls').unlink(missing_ok=True)
        r = self.sh(shell, body + '\nat "AT+CSQ" 4', MU300_AT_DIR='', SIM_SLOT='0',
                    MAIN_DIR=run / 'mu300-at', DATA_DIR=run / 'mu300-at2')
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().strip()

    def test_at_prefers_the_nr2_daemon(self):
        for shell in self.each_shell():
            out = self.run_at(shell, nr2=True)
            self.assertRegex(out, r'^dir=\S*/run/mu300-at2 args=-t 4 AT\+CSQ$', shell)
            # the nr1 daemon takes over while nr2's is not there (yet): mu300-at's own default directory
            out = self.run_at(shell, nr2=False)
            self.assertRegex(out, r'^dir=\S*/run/mu300-at args=-t 4 AT\+CSQ$', shell)

    # mobile-data's functions with MU300_LIB=1 (K56, K59-K62). mobile-data is a bash script (#!/bin/bash, on OpenWrt
    # too), so these run under bash only; under alpine they still use busybox's tail, wc, sleep and awk.
    # `at` is replaced by the modem below, which answers from a table and writes each command it was sent to
    # $STUBLOG/at as "<seconds since the start> <command>".
    MODEM = r'''
at() {
    echo "$SECONDS $1" >> "$STUBLOG/at"
    case $1 in
        'AT+CEREG?') printf '%s\nOK\n' "${CEREG:-+CEREG: 2,2}" ;;
        'AT+C5GREG?') [ -z "${C5GREG:-}" ] || printf '%s\n' "$C5GREG"; printf 'OK\n' ;;
        'AT+CFUN?') printf '+CFUN: 1\nOK\n' ;;
        AT+CGCONTRDP=*)
            # the address arrives once CGACT=1 has been sent $ADDR_AFTER times
            if [ "$(grep -c 'AT+CGACT=1' "$STUBLOG/at")" -ge "${ADDR_AFTER:-1}" ]; then
                printf '%s\nOK\n' "${RDP:-+CGCONTRDP: 1,5,\"apn\",\"10.1.2.3.255.255.255.0\",\"10.1.2.1\",\"8.8.8.8\",\"1.1.1.1\"}"
            else
                printf '+CGCONTRDP: 1,5,"apn","0.0.0.0.0.0.0.0","0.0.0.0","0.0.0.0","0.0.0.0"\nOK\n'
            fi ;;
        AT+CGDATA=*) printf 'CONNECT\n' ;;
        AT+CGPADDR=*) printf '+CGPADDR: 1,"10.1.2.3"\nOK\n' ;;
        'AT+COPS?') printf '+COPS: 0,0,"Test Net",7\nOK\n' ;;
        *) printf 'OK\n' ;;
    esac
}
'''

    def lib(self, code, modem=None, **env):
        """Run CODE with bash after sourcing mobile-data (MU300_LIB=1) and replacing `at` by MODEM (or MODEM).
        /run is the scratch directory's run/; mu300-led and mu300-at are stubs on PATH. The mu300-at stub is the nr1
        daemon's client that radio_on's RIL handshake calls directly: it logs "nr1 <command>" to $STUBLOG/at and
        answers $SMMSWAP (OK by default; "none" is a timeout, "busy" the client lock held)."""
        if not shutil.which('bash'):
            self.skipTest('no bash')
        text = (BIN / 'mobile-data').read_text()
        text = text.replace('/run/', f'{self.tmp}/run/').replace('/opt/mu300/bin/mu300-led', 'mu300-led')
        text = text.replace('/opt/mu300/bin/mu300-at ', 'mu300-at ')
        # K47: the delegate loader is a stub that logs "- sipa-dele-start wait=<MU300_DELE_WAIT>" in the AT order and
        # exits $DELE_RC (0 by default); /proc/modules is the scratch directory's proc-modules
        text = text.replace('/opt/mu300/bin/sipa-dele-start', 'sipa-dele-start')
        text = text.replace('/proc/modules', f'{self.tmp}/proc-modules')
        text = text.replace('/tmp/mu300-sipa-dele.log', f'{self.tmp}/sipa-dele.log')
        # R35: /lib/modules is the scratch directory's lib-modules, where this kernel has sipa-dele.ko unless a test
        # removes it; bootmark is a stub that appends its mark to $STUBLOG/marks
        text = text.replace('/lib/modules/', f'{self.tmp}/lib-modules/')
        text = text.replace('/opt/mu300/bin/bootmark', f'{self.tmp}/bootmark')
        if not (self.tmp / 'bootmark').exists():
            ko = self.tmp / 'lib-modules' / os.uname().release / 'sipa-dele.ko'
            ko.parent.mkdir(parents=True, exist_ok=True)
            ko.write_text('')
            (self.tmp / 'bootmark').write_text('#!/bin/sh\necho "$*" >> "$STUBLOG/marks"\n')
            (self.tmp / 'bootmark').chmod(0o755)
        lib = self.tmp / 'mobile-data.lib'
        lib.write_text(text)
        (self.tmp / 'run').mkdir(exist_ok=True)
        for f in ('at', 'led', 'rf', 'nr1env'):
            (self.tmp / f).unlink(missing_ok=True)
        self.stub('mu300-led', 'echo "$*" >> "$STUBLOG/led"')
        self.stub('sipa-dele-start', 'echo "- sipa-dele-start wait=$MU300_DELE_WAIT" >> "$STUBLOG/at"\n'
                  'echo "sipa-dele-start: stub"\nexit "${DELE_RC:-0}"')
        self.stub('mu300-at', '[ "$1" = -t ] && shift 2\necho "nr1 $1" >> "$STUBLOG/at"\n'
                  'echo "dir=$MU300_AT_DIR wait=$MU300_AT_LOCK_WAIT" > "$STUBLOG/nr1env"\n'
                  'case ${SMMSWAP:-OK} in none) echo "mu300-at: no answer from the daemon" >&2; exit 1 ;;\n'
                  '    busy) echo "mu300-at: busy" >&2; exit 1 ;; esac\n'
                  'echo "${SMMSWAP:-OK}"')
        r = self.sh(['bash'], f'MU300_LIB=1; . "{lib}"\n{modem or self.MODEM}\nSECONDS=0\n{code}', **env)
        sent = (self.tmp / 'at').read_text().splitlines() if (self.tmp / 'at').exists() else []
        return r, sent

    def test_registered_from_urc(self):
        """nr0 announces the registration: wait_registered sees it in the URC log and asks nothing in the first 5 s."""
        log = self.tmp / 'stty_nr0.log'
        # the modem's last word before the call was "searching"; the registration is appended a second later
        log.write_text('+CEREG: 2\n')
        r, sent = self.lib('(sleep 1; printf \'+CEREG: 1,"1A2B","0123ABCD",7\\n\' >> "$MU300_URC_LOG") &\n'
                           'rc=0; wait_registered || rc=$?; echo "rc=$rc t=$SECONDS"',
                           MU300_URC_LOG=log, MU300_REGISTER_WAIT=20, CEREG='+CEREG: 2,2')
        self.assertRegex(r.stdout, r'rc=0 t=[0-4]\b', r.stderr)
        self.assertFalse([s for s in sent if 'AT+CEREG?' in s], sent)
        # mu300-atd starts the log over at its size cap: a registration written after the cut still counts
        log.write_text('+CEREG: 2\n' * 50)
        r, sent = self.lib('(sleep 1; : > "$MU300_URC_LOG"; printf \'+CEREG: 5\\n\' >> "$MU300_URC_LOG") &\n'
                           'rc=0; wait_registered || rc=$?; echo "rc=$rc t=$SECONDS"',
                           MU300_URC_LOG=log, MU300_REGISTER_WAIT=20, CEREG='+CEREG: 2,2')
        self.assertRegex(r.stdout, r'rc=0 t=[0-4]\b', r.stderr)
        self.assertFalse([s for s in sent if 'AT+CEREG?' in s], sent)

    def test_old_urc_does_not_count(self):
        """A registration announced before the call proves nothing now: the query decides, and is sent at once."""
        log = self.tmp / 'stty_nr0.log'
        log.write_text('+CEREG: 2\n+CEREG: 1,"1A2B","0123ABCD",7\n')
        r, sent = self.lib('rc=0; wait_registered || rc=$?; echo "rc=$rc t=$SECONDS"',
                           MU300_URC_LOG=log, MU300_REGISTER_WAIT=20, CEREG='+CEREG: 2,1,"1A2B","0123ABCD",7')
        self.assertRegex(r.stdout, r'rc=0 t=[01]\b', r.stderr)
        self.assertTrue([s for s in sent if 'AT+CEREG?' in s], sent)
        # nothing new arrives and the query says "searching": not registered when the budget is spent. "+CEREG: 1,2"
        # is the query form with <n>=1 and <stat>=2, not a registration.
        r, sent = self.lib('rc=0; wait_registered || rc=$?; echo "rc=$rc t=$SECONDS"',
                           MU300_URC_LOG=log, MU300_REGISTER_WAIT=6, CEREG='+CEREG: 1,2')
        self.assertRegex(r.stdout, r'rc=1 t=[6-8]\b', r.stderr)
        # the fallback asks every 5 s, not every 2
        self.assertEqual(len([s for s in sent if 'AT+CEREG?' in s]), 2, sent)

    def test_fetch_addr_bounded(self):
        """+CGCONTRDP keeps answering 0.0.0.0: fetch_addr gives up within its budget (in seconds, not rounds), and
        does not sleep past it (queries at 0 and 2 s; a third would start after the budget, so none is waited for)."""
        # (the budget is counted on whole seconds of the clock, $SECONDS on whole seconds since the shell started: a
        # start late in a second shows a run of the same length as 1 on a fast machine - 1 to 3 is within the budget)
        r, _ = self.lib('rc=0; fetch_addr 3 || rc=$?; echo "rc=$rc t=$SECONDS rdp=$rdp"', ADDR_AFTER=99)
        self.assertRegex(r.stdout, r'rc=1 t=[123] rdp=$', r.stderr)
        r, _ = self.lib('rc=0; fetch_addr 3 || rc=$?; echo "rc=$rc rdp=$rdp"', ADDR_AFTER=0)
        self.assertIn('rc=0 rdp=+CGCONTRDP: 1,5,"apn","10.1.2.3.255.255.255.0"', r.stdout, r.stderr)

    def test_address_after_one_cgact_reassert(self):
        """up (netifd mode): no AT+CGACT? first, one idempotent CGACT=1 reassert when the address is late, never
        CGACT=0, and the decision is in radio.log (K60, K62)."""
        r, sent = self.lib('up_locked', MU300_NETIFD=1, MU300_AT_DEV='/dev/null', MU300_URC_LOG=self.tmp / 'none',
                           CEREG='+CEREG: 2,1,"1A2B","0123ABCD",7', ADDR_AFTER=2, MU300_CFUN_WAIT=0,
                           MU300_ADDR_WAIT_FIRST=2, MU300_ADDR_WAIT_RETRY=6)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('IP=10.1.2.3\nPREFIX=24\nDNS1=8.8.8.8\nDNS2=1.1.1.1\n', r.stdout)
        cmds = [s.split(' ', 1)[1] for s in sent]
        # K58: the IMS bearer SMS needs, once the radio is on and before the registration wait
        self.assertLess(cmds.index('AT+CFUN?'), cmds.index('AT+CAVIMS=1'), cmds)
        self.assertLess(cmds.index('AT+CAVIMS=1'), cmds.index('AT+CEREG?'), cmds)
        self.assertEqual(cmds.count('AT+CGACT=1,1'), 2, cmds)
        self.assertNotIn('AT+CGACT?', cmds)
        self.assertFalse([c for c in cmds if c.startswith('AT+CGACT=0')], cmds)
        radio = (self.tmp / 'run' / 'mu300' / 'radio.log').read_text()
        self.assertRegex(radio, r'(?m)^t=\S* +registered\b')
        self.assertIn('reasserting', radio)

    def test_sipa_dele_between_registration_and_cgact(self):
        """K47: the IPA delegate is loaded by the dial itself, after the registration and before the PDP context
        (AT+CGACT=1), with a 20 s wait; when it does not load, the dial stops there and activates nothing; once it
        is loaded, a later dial does not call the loader again."""
        env = dict(MU300_NETIFD=1, MU300_AT_DEV='/dev/null', MU300_URC_LOG=self.tmp / 'none', MU300_CFUN_WAIT=0,
                   CEREG='+CEREG: 2,1,"1A2B","0123ABCD",7')
        r, sent = self.lib('up_locked', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('IP=10.1.2.3\n', r.stdout)
        self.assertNotIn('stub', r.stdout)                      # its output does not reach netifd's parser
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(cmds.count('sipa-dele-start wait=20'), 1, cmds)
        dele = cmds.index('sipa-dele-start wait=20')
        last_cereg = len(cmds) - 1 - cmds[::-1].index('AT+CEREG?')
        self.assertLess(last_cereg, dele, cmds)                    # after the registration
        self.assertLess(dele, cmds.index('AT+CGACT=1,1'), cmds)    # before the PDP context
        # the loader refuses (the packet domain never came up, or insmod failed): no CGACT, exit 1, said why
        r, sent = self.lib('up_locked', DELE_RC=1, **env)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('sipa-dele-start wait=20', cmds)
        self.assertFalse([c for c in cmds if c.startswith('AT+CGACT')], cmds)
        self.assertNotIn('IP=', r.stdout)
        self.assertIn('IPA delegate', r.stderr)
        self.assertIn('IPA delegate', (self.tmp / 'run' / 'mu300' / 'radio.log').read_text())
        # already in /proc/modules (a reconnect): straight on to the context, the loader is not called
        (self.tmp / 'proc-modules').write_text('sipa_dele 16384 0 - Live 0x0000000000000000 (O)\n')
        r, sent = self.lib('up_locked', DELE_RC=1, **env)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([c for c in cmds if c.startswith('sipa-dele-start')], cmds)
        self.assertIn('AT+CGACT=1,1', cmds)

    def test_up_applies_the_ttl(self):
        """Every bring-up (netifd, the watchdog's reconnect) re-applies mu300-ttl's rule: the tc backend's filters
        belong to the interface. Its output never reaches netifd's parser, and its failure fails nothing."""
        env = dict(MU300_NETIFD=1, MU300_AT_DEV='/dev/null', MU300_URC_LOG=self.tmp / 'none', MU300_CFUN_WAIT=0,
                   CEREG='+CEREG: 2,1,"1A2B","0123ABCD",7', MU300_TTL_CMD=self.stubs / 'ttl')
        for rc in (0, 1):
            self.stub('ttl', f'echo "$*" >> "$STUBLOG/ttl.log"; echo noise; echo err >&2; exit {rc}')
            (self.tmp / 'ttl.log').unlink(missing_ok=True)
            r, _ = self.lib('up_locked', **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('IP=10.1.2.3\n', r.stdout)
            self.assertNotIn('noise', r.stdout)
            self.assertEqual((self.tmp / 'ttl.log').read_text(), 'apply\n')

    def test_sipa_dele_only_with_the_module(self):
        """R35: a kernel without sipa-dele.ko (neither in /lib/modules/<release> nor under extra/) dials without the
        loader, and the boot timeline has no dial-sipa-dele mark; with the module (either place) the loader runs and
        the mark is written."""
        env = dict(MU300_NETIFD=1, MU300_AT_DEV='/dev/null', MU300_URC_LOG=self.tmp / 'none', MU300_CFUN_WAIT=0,
                   CEREG='+CEREG: 2,1,"1A2B","0123ABCD",7')
        self.lib(':')                                   # makes lib-modules and the bootmark stub
        mods = self.tmp / 'lib-modules' / os.uname().release
        (mods / 'sipa-dele.ko').unlink()
        r, sent = self.lib('up_locked', **env)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('IP=10.1.2.3\n', r.stdout)
        self.assertFalse([c for c in cmds if c.startswith('sipa-dele-start')], cmds)
        self.assertIn('AT+CGACT=1,1', cmds)
        marks = (self.tmp / 'marks').read_text().split()
        self.assertIn('dial-registered', marks)
        self.assertNotIn('dial-sipa-dele', marks)
        self.assertIn('no sipa-dele.ko', self.radio_log())
        (mods / 'extra').mkdir()
        (mods / 'extra' / 'sipa-dele.ko').write_text('')
        (self.tmp / 'marks').unlink()
        r, sent = self.lib('up_locked', **env)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sipa-dele-start wait=20', cmds)
        self.assertIn('dial-sipa-dele', (self.tmp / 'marks').read_text().split())

    # R35: up()'s lock, the radio lock's scheme. up_locked is replaced by a round that logs "start" and "end" to
    # $STUBLOG/rounds around $ROUND s (1) of sleep.
    ROUND = ('up_locked() { echo start >> "$STUBLOG/rounds"; sleep "${ROUND:-1}"; echo end >> "$STUBLOG/rounds"; }\n')

    def up_lock_files(self):
        return sorted(p.name for p in (self.tmp / 'run').iterdir() if p.name.startswith('mu300-mobile-data-up'))

    def test_up_lock_holder_is_named_from_the_start(self):
        """R35: the bring-up lock never exists without its owner's pid, and is gone after the round."""
        r, _ = self.lib(self.ROUND + 'ROUND=2; ( up ) & h=$!\n'
                        f'sleep 1; echo "owner=$(readlink "{self.tmp}/run/mu300-mobile-data-up.owner") h=$h"; wait $h')
        m = re.search(r'owner=(\d+) h=(\d+)', r.stdout)
        self.assertTrue(m, r.stdout + r.stderr)
        self.assertEqual(m.group(1), m.group(2))
        self.assertEqual(self.up_lock_files(), [])

    def test_two_waiters_on_a_dead_up_lock(self):
        """R35: the bring-up's owner was killed in its round (nothing released). Two callers of up find the lock at
        the same moment: exactly one takes it over, the other waits for that one's round to end, and nothing of the
        lock is left afterwards (the old directory lock let the second remove the lock the first had just taken)."""
        r, _ = self.lib(self.ROUND + self.SLOW_DEAD +
                        'ROUND=5; ( up ) >/dev/null 2>&1 & h=$!\n'
                        'sleep 1; kill -9 $h; wait $h 2>/dev/null || true\n'
                        'ROUND=1\n'
                        '( DEAD_DELAY=0.2; up ) & a=$!; ( DEAD_DELAY=0.8; up ) & b=$!\n'
                        'ra=0; wait $a || ra=$?; rb=0; wait $b || rb=$?; echo "ra=$ra rb=$rb"')
        self.assertIn('ra=0 rb=0', r.stdout, r.stderr)
        rounds = [line.split()[0] for line in (self.tmp / 'rounds').read_text().splitlines()]
        # the killed owner's start, then two rounds one after the other, never two at once
        self.assertEqual(rounds, ['start', 'start', 'end', 'start', 'end'], rounds)
        self.assertEqual(self.up_lock_files(), [])

    def test_up_lock_wait_gives_up(self):
        """R35: a bring-up that holds the lock past the wait (MU300_UP_LOCK_WAIT, 150 s by default) makes the
        second caller give up and say whose it is; down() leaves a running bring-up's AT channel alone."""
        r, sent = self.lib(self.ROUND + 'ROUND=4; ( up ) & h=$!\n'
                           'sleep 1; rc=0; up 2>"$STUBLOG/err" || rc=$?; MU300_AT_DEV=/dev/null down\n'
                           'echo "rc=$rc h=$h"; wait $h', MU300_UP_LOCK_WAIT=1)
        m = re.search(r'rc=(\d+) h=(\d+)', r.stdout)
        self.assertTrue(m, r.stdout + r.stderr)
        self.assertEqual(m.group(1), '1')
        self.assertIn(f'(pid {m.group(2)})', (self.tmp / 'err').read_text())
        self.assertFalse([s for s in sent if 'AT+CGACT=0' in s], sent)

    def test_led_from_cereg(self):
        """4G or 5G from the AcT field of AT+CEREG? (K56), not from AT+COPS?; when that names no NR access technology,
        AT+C5GREG? tells a 5G SA registration when LTE is not registered (FINDINGS 199, kanoqwq's network-led.sh)."""
        for act, want in (('13', 'data 5g'), ('11', 'data 5g')):
            r, sent = self.lib('led_up', CEREG=f'+CEREG: 2,1,"1A2B","0123ABCD",{act}')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'led').read_text().strip(), want, act)
            self.assertEqual([s.split(' ', 1)[1] for s in sent], ['AT+CEREG?'], act)
        for cereg, c5g, want, asked in (('+CEREG: 2,1,"1A2B","0123ABCD",7', '+C5GREG: 2,1', 'data on', False),
                                        ('+CEREG: 2,5,"1A2B","0123ABCD",7', '', 'data on', False),
                                        ('+CEREG: 2,2', '+C5GREG: 2,1,"1A2B","0123ABCD",11', 'data 5g', True),
                                        ('+CEREG: 2,0', '+C5GREG: 2,5,"1A2B","0123ABCD",11', 'data 5g', True),
                                        ('+CEREG: 0,4', '+C5GREG: 0,2', 'data on', True),
                                        ('+CEREG: 2,2', '', 'data on', True)):
            r, sent = self.lib('led_up', CEREG=cereg, C5GREG=c5g)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'led').read_text().strip(), want, (cereg, c5g))
            self.assertEqual([s.split(' ', 1)[1] for s in sent], ['AT+CEREG?'] + (['AT+C5GREG?'] if asked else []), cereg)

    def test_systemd_path_keeps_v6_up(self):
        """K63 is rejected: on the systemd path (Ubuntu) an IPv6 address from the network still runs v6_up, and none
        switches IPv6 off."""
        text = (BIN / 'mobile-data').read_text()
        start = text.index('\nup_locked() {')
        body = text[start:text.index('\ndown() {', start)]   # the nft here-documents have their own '}' lines
        netifd = body.index('if [ "${MU300_NETIFD:-0}" = 1 ]; then')
        self.assertIn('[ -n "$iid" ] || v6_off', body[:netifd])
        self.assertIn('[ -n "$iid" ] && v6_up "$iid"', body[netifd:])
        self.assertNotIn('MU300_PDP_TYPE:-IP}" != IP', body)   # the fork's replacement for v6_up

    # radio_on (K57, K65, K66). The radio's state: "+CFUN: $CFUN" (0 by default, "none" = no answer) until AT+SFUN=4
    # has been answered, then $RF_LAG more "+CFUN: 0" and "+CFUN: 1" from then on. AT+SFUN=4 takes $SFUN_DELAY s.
    RADIO = r'''
at() {
    echo "$SECONDS $1" >> "$STUBLOG/at"
    case $1 in
        'AT+CFUN?')
            [ "${CFUN:-}" = none ] && { echo "mu300-at: modem returned no final response" >&2; return 1; }
            if [ -e "$STUBLOG/rf" ]; then
                k=$(cat "$STUBLOG/rf")
                if [ "$k" -gt 0 ]; then echo $((k - 1)) > "$STUBLOG/rf"; printf '+CFUN: 0\nOK\n'
                else printf '+CFUN: 1\nOK\n'; fi
            else
                printf '+CFUN: %s\nOK\n' "${CFUN:-0}"
            fi ;;
        'AT+SFUN=4') sleep "${SFUN_DELAY:-0}"; echo "${RF_LAG:-0}" > "$STUBLOG/rf"; printf 'OK\n' ;;
        *) printf 'OK\n' ;;
    esac
}
'''

    def radio(self, code, urc='+CEREG: 2\n', **env):
        """CODE against RADIO, with nr0's URC log already written (URC=None: no log) and no plugin unless the test
        names one (MU300_PLUGIN_LOCK)."""
        log = self.tmp / 'stty_nr0.log'
        log.unlink(missing_ok=True)
        if urc is not None:
            log.write_text(urc)
        env.setdefault('MU300_PLUGIN_LOCK', self.tmp / 'no-plugin')
        r, sent = self.lib(code, modem=self.RADIO, MU300_URC_LOG=log, **env)
        return r, sent, [s.split(' ', 1)[1] for s in sent]

    def radio_log(self):
        p = self.tmp / 'run' / 'mu300' / 'radio.log'
        return p.read_text() if p.exists() else ''

    def test_ril_handshake_once_per_boot(self):
        """AT+SMMSWAP=0 is nr1's first command, sent through the nr1 daemon once per boot (K57)."""
        marker = self.tmp / 'run' / 'mu300-ril-handshake'
        r, sent, cmds = self.radio('radio_on; radio_on; echo ok', RF_LAG=0)
        self.assertIn('ok', r.stdout, r.stderr)
        self.assertEqual(cmds.count('AT+SMMSWAP=0'), 1, cmds)
        self.assertEqual(cmds[0], 'AT+SMMSWAP=0', cmds)
        self.assertTrue(sent[0].startswith('nr1 '), sent)       # mu300-at to nr1, not at() (nr2)
        self.assertEqual((self.tmp / 'nr1env').read_text().strip(), f'dir={self.tmp}/run/mu300-at wait=1')
        self.assertTrue(marker.exists())
        self.assertEqual(cmds.count('AT+SFUN=4'), 1, cmds)       # the second call found the radio on
        # no answer to the handshake: the round ends there, nothing else is sent and the next round tries again
        marker.unlink()
        r, sent, cmds = self.radio('rc=0; radio_on || rc=$?; echo "rc=$rc"', SMMSWAP='none')
        self.assertIn('rc=1', r.stdout, r.stderr)
        self.assertEqual(cmds, ['AT+SMMSWAP=0'])
        self.assertFalse(marker.exists())
        self.assertIn('RIL handshake', self.radio_log())

    def test_ril_handshake_error_is_an_answer(self):
        """Final review minor 2: a firmware that refuses AT+SMMSWAP=0 still answers from a live channel. The refusal
        is logged, the handshake counts as done for this boot, and the round goes on to CFUN; only no answer at all
        (a timeout, the lock busy, no final result code) ends the round."""
        marker = self.tmp / 'run' / 'mu300-ril-handshake'
        for answer in ('ERROR', '+CME ERROR: 4'):
            with self.subTest(answer=answer):
                marker.unlink(missing_ok=True)
                (self.tmp / 'run' / 'mu300' / 'radio.log').unlink(missing_ok=True)
                r, sent, cmds = self.radio('rc=0; radio_on || rc=$?; echo "rc=$rc"', SMMSWAP=answer, RF_LAG=0)
                self.assertIn('rc=0', r.stdout, r.stderr)
                self.assertEqual(cmds[:3], ['AT+SMMSWAP=0', 'AT+CFUN?', 'AT+SFUN=4'], cmds)
                self.assertTrue(marker.exists())
                self.assertIn(f'refused the RIL handshake (AT+SMMSWAP=0): {answer}', self.radio_log())
        for answer in ('busy', 'garbage'):
            with self.subTest(answer=answer):
                marker.unlink(missing_ok=True)
                r, sent, cmds = self.radio('rc=0; radio_on || rc=$?; echo "rc=$rc"', SMMSWAP=answer)
                self.assertIn('rc=1', r.stdout, r.stderr)
                self.assertEqual(cmds, ['AT+SMMSWAP=0'])
                self.assertFalse(marker.exists())

    def test_waits_for_nr0_first(self):
        """Nothing is sent before nr0 has said something (the CP is still starting); the wait is bounded."""
        r, sent, cmds = self.radio(f'(sleep 1; echo "+CEREG: 2" > "{self.tmp}/stty_nr0.log") &\nradio_on; echo ok',
                                   urc=None, MU300_CFUN_WAIT=10)
        self.assertIn('ok', r.stdout, r.stderr)
        first = [s for s in sent if s[0].isdigit()][0]
        self.assertGreaterEqual(int(first.split()[0]), 1, sent)
        # nr0 never speaks: go ahead after MU300_CFUN_WAIT s anyway
        r, sent, cmds = self.radio('radio_on; echo "ok t=$SECONDS"', urc=None, MU300_CFUN_WAIT=1)
        self.assertRegex(r.stdout, r'ok t=[12]\b', r.stderr)
        self.assertIn('AT+SFUN=4', cmds)

    def test_no_sfun_when_cfun_is_unanswered(self):
        """A channel that does not answer AT+CFUN? gets no AT+SFUN either (K57)."""
        r, sent, cmds = self.radio('rc=0; radio_on || rc=$?; echo "rc=$rc"', CFUN='none')
        self.assertIn('rc=1', r.stdout, r.stderr)
        self.assertFalse([c for c in cmds if c.startswith('AT+SFUN')], cmds)
        self.assertIn('no answer to AT+CFUN?', self.radio_log())

    def test_radio_comes_up_late_without_a_power_cycle(self):
        """After AT+SFUN=4 the radio says 0 twice and then 1: wait for it, no SFUN=2 off/on cycle (K57)."""
        r, sent, cmds = self.radio('rc=0; radio_on || rc=$?; echo "rc=$rc t=$SECONDS"', RF_LAG=2)
        self.assertRegex(r.stdout, r'rc=0 t=[4-6]\b', r.stderr)
        self.assertEqual(cmds.count('AT+SFUN=4'), 1, cmds)
        self.assertNotIn('AT+SFUN=2', cmds)
        self.assertEqual(cmds.count('AT+CFUN?'), 4, cmds)     # before, then 0, 0, 1
        self.assertRegex(self.radio_log(), r'radio on [4-6] s after AT\+SFUN=4')
        # already on (and the handshake done this boot): one question, nothing switched
        r, sent, cmds = self.radio('radio_on; echo ok', CFUN=1)
        self.assertEqual(cmds, ['AT+CFUN?'])

    def test_two_radio_on_one_state_machine(self):
        """The warm-up and the dial meet: the second radio_on waits for the first instead of running its own CFUN/SFUN
        sequence beside it, and then finds the radio on (K57)."""
        r, sent, cmds = self.radio('radio_on & a=$!; radio_on & b=$!\n'
                                   'ra=0; wait $a || ra=$?; rb=0; wait $b || rb=$?; echo "ra=$ra rb=$rb"',
                                   SFUN_DELAY=1)
        self.assertIn('ra=0 rb=0', r.stdout, r.stderr)
        self.assertEqual(cmds.count('AT+SMMSWAP=0'), 1, cmds)
        self.assertEqual(cmds.count('AT+SFUN=4'), 1, cmds)
        self.assertEqual(cmds.count('AT+CFUN?'), 3, cmds)    # the first: 0, then 1; the second: 1
        self.assertEqual(self.radio_lock_files(), [])

    def radio_lock_files(self):
        return sorted(p.name for p in (self.tmp / 'run').iterdir() if p.name.startswith('mu300-radio-on'))

    # R32: radio_on's lock. A wrapped lock_owner_alive takes $DEAD_DELAY s (0.5) more over every "dead" verdict and
    # $ALIVE_DELAY s (0) over every "alive" one: two waiters can both see the dead owner before either acts on it.
    SLOW_DEAD = ('eval "orig_$(declare -f lock_owner_alive)"\n'
                 'lock_owner_alive() { if orig_lock_owner_alive "$@"; then sleep "${ALIVE_DELAY:-0}"; return 0; fi;'
                 ' sleep "${DEAD_DELAY:-0.5}"; return 1; }\n')

    def test_two_waiters_on_a_dead_owners_lock(self):
        """R32: the lock's owner was killed in the middle of its round (SIGKILL: nothing released). Two radio_on
        callers find it at the same moment: exactly one of them takes it over and runs the radio state machine,
        the other waits for that one and finds the radio on; nothing of the lock is left afterwards. The second one
        acts on its "dead" verdict only once the first has taken the lock over (the old lock removed that one)."""
        r, sent, cmds = self.radio(
            self.SLOW_DEAD +
            'SFUN_DELAY=5; ( radio_on ) >/dev/null 2>&1 & h=$!\n'
            'sleep 1; kill -9 $h; wait $h 2>/dev/null || true\n'
            'SFUN_DELAY=1\n'
            '( DEAD_DELAY=0.2; radio_on ) & a=$!; ( DEAD_DELAY=0.8; radio_on ) & b=$!\n'
            'ra=0; wait $a || ra=$?; rb=0; wait $b || rb=$?; echo "ra=$ra rb=$rb"', CFUN=0)
        self.assertIn('ra=0 rb=0', r.stdout, r.stderr)
        # the killed owner's AT+SFUN=4, then one more from the one that took over; never a third
        self.assertEqual(cmds.count('AT+SFUN=4'), 2, cmds)
        self.assertEqual(self.radio_lock_files(), [])

    def test_lock_holder_is_named_from_the_start(self):
        """R32: the lock never exists without its owner's pid (no window in which a waiter reads an empty owner
        and calls the lock dead)."""
        r, sent, cmds = self.radio(
            'SFUN_DELAY=2; ( radio_on ) >/dev/null 2>&1 & h=$!\n'
            f'sleep 1; echo "owner=$(readlink "{self.tmp}/run/mu300-radio-on.owner") h=$h"; wait $h')
        m = re.search(r'owner=(\d+) h=(\d+)', r.stdout)
        self.assertTrue(m, r.stdout + r.stderr)
        self.assertEqual(m.group(1), m.group(2))
        self.assertEqual(self.radio_lock_files(), [])

    def test_radio_lock_wait_is_in_seconds(self):
        """R32: MU300_RADIO_LOCK_WAIT is a deadline in real seconds, not a number of polls: with a slow liveness
        check (0.3 s each) a 2 s wait still ends after about 2 s."""
        r, sent, cmds = self.radio(
            self.SLOW_DEAD +
            'SFUN_DELAY=8; ( radio_on ) >/dev/null 2>&1 & h=$!\n'
            'sleep 1; ALIVE_DELAY=0.3; t0=$SECONDS; rc=0; radio_on || rc=$?; t=$((SECONDS - t0))\n'
            'kill -9 $h; echo "rc=$rc t=$t"', MU300_RADIO_LOCK_WAIT=2)
        self.assertRegex(r.stdout, r'rc=1 t=[23]\b', r.stderr)
        self.assertIn('still running; giving up', self.radio_log())

    def test_early_lock_replay_hook(self):
        """K65: the plugin's lock replay runs after the handshake and the radio-off answer, before AT+SFUN=4; only
        when the plugin is installed and has not replayed yet; a failed replay never stops the radio."""
        hook = self.tmp / 'lock'
        hook.write_text('#!/bin/sh\necho "- hook $*" >> "$STUBLOG/at"\nexit "${HOOK_RC:-0}"\n')
        hook.chmod(0o755)
        pending = self.tmp / 'run' / 'unisoc-modem-early-hook-pending'
        pending.parent.mkdir(exist_ok=True)
        pending.touch()
        r, sent, cmds = self.radio('radio_on; echo ok', MU300_PLUGIN_LOCK=hook)
        self.assertIn('ok', r.stdout, r.stderr)
        self.assertEqual(cmds[:4], ['AT+SMMSWAP=0', 'AT+CFUN?', 'hook replay early', 'AT+SFUN=4'], cmds)
        self.assertFalse(pending.exists())          # the plugin's generic worker may use nr1 now
        # no plugin: no hook
        r, sent, cmds = self.radio('radio_on; echo ok')
        self.assertNotIn('hook replay early', cmds)
        self.assertIn('AT+SFUN=4', cmds)
        # the plugin already replayed its locks this boot
        (self.tmp / 'run' / 'unisoc-modem-lock-replay-done').touch()
        r, sent, cmds = self.radio('radio_on; echo ok', MU300_PLUGIN_LOCK=hook)
        self.assertNotIn('hook replay early', cmds)
        (self.tmp / 'run' / 'unisoc-modem-lock-replay-done').unlink()
        # a failed replay: said in radio.log, the radio comes on all the same
        r, sent, cmds = self.radio('radio_on; echo ok', MU300_PLUGIN_LOCK=hook, HOOK_RC=1)
        self.assertIn('ok', r.stdout, r.stderr)
        self.assertIn('AT+SFUN=4', cmds)
        self.assertIn('early lock replay unverified; late fallback remains armed', self.radio_log())
        # a failed radio_on keeps the marker: the worker stays away from nr1 until a round succeeds
        pending.touch()
        r, sent, cmds = self.radio('radio_on || echo failed', CFUN='none', MU300_PLUGIN_LOCK=hook)
        self.assertIn('failed', r.stdout, r.stderr)
        self.assertNotIn('hook replay early', cmds)
        self.assertTrue(pending.exists())

    # suspend/resume: mu300-power takes the modem down while nobody uses the device. radio_on and up are replaced by a
    # line in the AT log (their own sequences are tested elsewhere); down is the real one, on the scratch run/.
    # radio_on also notes whether the mode file is still there; AT+SPENDC? answers "+ENDC: $ENDC" (1 by default).
    SUSPEND_STUBS = ('radio_on() { at RADIO_ON; [ ! -e "$SUSPEND" ] || at SUSPEND_FILE_STILL_THERE; }\n'
                     'up() { at UP; [ ! -e "$SUSPEND" ] || at SUSPEND_FILE_STILL_THERE; }\n')

    def suspend_lib(self, code, **env):
        modem = self.MODEM.replace("        'AT+COPS?')",
                                   "        'AT+SPENDC?') printf '+ENDC: %s\\nOK\\n' \"${ENDC:-1}\" ;;\n        'AT+COPS?')")
        return self.lib(self.SUSPEND_STUBS + code, modem=modem, MU300_AT_DEV='/dev/null', **env)

    def test_suspend_off_takes_the_radio_down_and_stops_the_watcher(self):
        r, sent = self.suspend_lib('do_suspend off; do_suspend off')
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.tmp / 'run'
        self.assertEqual((run / 'mu300-mobile-data.suspend').read_text().strip(), 'off')
        self.assertTrue((run / 'mu300-mobile-data-down').exists())
        # the second call found the file and did nothing more
        self.assertEqual(len([c for c in sent if 'AT+SFUN=5' in c]), 1, sent)
        self.assertFalse((run / 'mu300-radio-on.owner').exists())   # the radio lock is released

    def test_suspend_lte_switches_endc_off_and_resume_restores(self):
        """EN-DC off is AT+SPENDC=2 and on is 1 (the lock adapter's values); no stack restart, radio_on or dial."""
        r, sent = self.suspend_lib('do_suspend lte\n'
                                   f'cat "{self.tmp}/run/mu300-mobile-data.suspend"; ls "{self.tmp}/run"\n'
                                   'echo ---; resume; echo resumed')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.split('---')[0]
        self.assertIn('lte\nmu300-mobile-data.suspend', out)
        self.assertNotIn('mu300-mobile-data-down', out)   # data stays up on LTE
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertEqual(cmds, ['AT+SPENDC?', 'AT+SPENDC=2', 'AT+SPENDC=1'])
        self.assertFalse((self.tmp / 'run' / 'mu300-radio-on.owner').exists())

    def test_resume_lte_leaves_endc_off_when_the_user_had_it_off(self):
        r, sent = self.suspend_lib('do_suspend lte; ls "$SUSPEND_ENDC" >/dev/null && echo noted; resume; echo resumed', ENDC='2')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('noted', r.stdout)
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertEqual(cmds, ['AT+SPENDC?'])   # already off: nothing written, nothing restored
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend-endc').exists())
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())

    def test_resume_off_brings_radio_and_data_back_and_drops_the_mode_file_last(self):
        r, sent = self.suspend_lib('do_suspend off; resume')
        self.assertEqual(r.returncode, 0, r.stderr)
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertEqual(cmds[-4:], ['RADIO_ON', 'SUSPEND_FILE_STILL_THERE', 'UP', 'SUSPEND_FILE_STILL_THERE'])
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data-down').exists())

    def test_suspend_waits_for_a_radio_dial_in_flight(self):
        """The raw AT sequence takes the radio lock: with it held (and no wait) nothing is sent and no mode is left."""
        r, sent = self.suspend_lib(f'ln -s $$ "{self.tmp}/run/mu300-radio-on.owner"; rc=0; do_suspend lte || rc=$?; echo rc=$rc',
                                   MU300_RADIO_LOCK_WAIT='0')
        self.assertIn('rc=1', r.stdout, r.stderr)
        self.assertEqual(sent, [])
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())

    # netifd (OpenWrt, MU300_NETIFD=1) owns the WAN's address, routes, DNS and firewall: suspend off takes it down
    # with ifdown and resume brings it back with ifup; neither touches sipa_eth0 itself. ifdown/ifup are stubs.
    def netifd_stubs(self):
        for name in ('ifdown', 'ifup', 'ip', 'nft'):
            self.stub(name, f'echo "{name} $* suspend=$([ -e "$MU300_RUN_DIR/mu300-mobile-data.suspend" ] && echo yes || echo no)" >> "$STUBLOG/calls"')

    def test_suspend_off_under_netifd_is_ifdown(self):
        self.netifd_stubs()
        r, sent = self.suspend_lib('do_suspend off', MU300_NETIFD='1', MU300_RUN_DIR=self.tmp / 'run')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text()
        self.assertIn('ifdown wan', calls)
        self.assertNotIn('ip ', calls); self.assertNotIn('nft', calls)   # down() was not run
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertEqual(cmds, ['AT+SFUN=5'])
        self.assertEqual((self.tmp / 'run' / 'mu300-mobile-data.suspend').read_text().strip(), 'off')

    def test_resume_under_netifd_redials_with_ifup(self):
        self.netifd_stubs()
        r, sent = self.suspend_lib('do_suspend off; resume', MU300_NETIFD='1', MU300_RUN_DIR=self.tmp / 'run')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text()
        # netifd's dial runs `mobile-data up` in a process of its own: the mode file is gone before the ifup
        self.assertIn('ifup wan suspend=no', calls)
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertIn('RADIO_ON', cmds); self.assertNotIn('UP', cmds)
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data-down').exists())

    def test_resume_nodial_switches_the_radio_on_and_leaves_the_dial_to_the_watcher(self):
        self.netifd_stubs()
        (self.tmp / 'run').mkdir(exist_ok=True)
        (self.tmp / 'run' / 'mu300-mobile-data-down').touch()
        r, sent = self.suspend_lib('do_suspend off; resume nodial', MU300_RUN_DIR=self.tmp / 'run')
        self.assertEqual(r.returncode, 0, r.stderr)
        cmds = [c.split(' ', 1)[1] for c in sent]
        self.assertIn('RADIO_ON', cmds); self.assertNotIn('UP', cmds)
        self.assertNotIn('ifup', (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else '')
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data-down').exists())
        # lte: EN-DC back on, the files gone, still no dial
        r, sent = self.suspend_lib('do_suspend lte; resume nodial')
        self.assertEqual([c.split(' ', 1)[1] for c in sent][-1], 'AT+SPENDC=1')
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend').exists())
        self.assertFalse((self.tmp / 'run' / 'mu300-mobile-data.suspend-endc').exists())

    def test_up_refuses_while_suspended_off_except_from_resume(self):
        (self.tmp / 'run').mkdir(exist_ok=True)
        (self.tmp / 'run' / 'mu300-mobile-data.suspend').write_text('off\n')
        # the real up; up_locked stands for the dial
        code = 'up_locked() { at DIAL; }\n'
        for env in ({}, {'MU300_NETIFD': '1'}):
            r, sent = self.lib(code + 'rc=0; up || rc=$?; echo rc=$rc', MU300_AT_DEV='/dev/null', **env)
            self.assertIn('rc=1', r.stdout, r.stderr)
            self.assertIn('suspended', r.stderr)
            self.assertEqual(sent, [])
        r, sent = self.lib(code + 'rc=0; ( _resuming=1; up ) || rc=$?; echo rc=$rc', MU300_AT_DEV='/dev/null')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertEqual([c.split(' ', 1)[1] for c in sent], ['DIAL'])
        # resume's own dial passes (redial sets the flag); suspend lte leaves up alone
        r, sent = self.lib(code + 'radio_on() { at RADIO_ON; }; resume', MU300_AT_DEV='/dev/null')
        self.assertEqual([c.split(' ', 1)[1] for c in sent], ['RADIO_ON', 'DIAL'], r.stderr)
        (self.tmp / 'run' / 'mu300-mobile-data.suspend').write_text('lte\n')
        r, sent = self.lib(code + 'up', MU300_AT_DEV='/dev/null')
        self.assertEqual([c.split(' ', 1)[1] for c in sent], ['DIAL'], r.stderr)

    def test_watch_skips_rounds_while_suspended(self):
        (self.tmp / 'run').mkdir(exist_ok=True)
        (self.tmp / 'run' / 'mu300-mobile-data.suspend').write_text('off\n')
        r, sent = self.lib('watch', MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0', MU300_WATCH_ROUNDS='2')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sent, [])

    # FINDINGS 90: AT+CGACT? as the watchdog reads it. $CGACT is the answer's lines ("none": no final result code,
    # mu300-at's exit 1); the interface has its address unless NOADDR is set. radio_on, up and ifup only log.
    WATCH_MODEM = r'''
at() {
    echo "$SECONDS $1" >> "$STUBLOG/at"
    case $1 in
        'AT+CGACT?')
            [ "${CGACT:-}" != none ] || return 1
            printf '%b\n' "${CGACT:-+CGACT:1,1\nOK}" ;;
        'AT+CFUN?') printf '+CFUN: 1\nOK\n' ;;
        AT+CGPADDR=*) printf '+CGPADDR: 1,"10.1.2.3"\nOK\n' ;;
        *) printf 'OK\n' ;;
    esac
}
radio_on() { at RADIO_ON; }
up() { at UP; }
'''

    def watch(self, rounds, **env):
        self.stub('ip', '[ -n "$NOADDR" ] || echo "2: sipa_eth0    inet 10.1.2.3/24 scope global sipa_eth0"')
        self.stub('ifup', 'echo "$SECONDS IFUP $*" >> "$STUBLOG/at"')
        r, sent = self.lib('watch', modem=self.WATCH_MODEM, MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0',
                           MU300_WATCH_ROUNDS=str(rounds), NOADDR=env.pop('NOADDR', ''), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r, [s.split(' ', 1)[1] for s in sent]

    def test_the_openwrt_watchdog_logs(self):
        # FINDINGS 90 (measured on a U30 Air): procd kept none of the watchdog's stderr, so on OpenWrt nothing it said
        # ("mobile data lost ... reconnecting", "no answer about the data context") ever reached logread
        post = (TOP / 'openwrt/overlay/etc/init.d/mu300-post').read_text()
        inst = post[post.index('procd_open_instance cellwatch'):]
        inst = inst[:inst.index('procd_close_instance')]
        self.assertIn('procd_set_param command /opt/mu300/bin/mobile-data watch', inst)
        self.assertIn('procd_set_param stderr 1', inst)

    def test_watch_context_states(self):
        cases = [('+CGACT:1,1\\nOK', 'active'), ('+CGACT: 1,1\\r\\nOK', 'active'),
                 ('+CGACT:1,0\\nOK', 'inactive'), ('OK', 'inactive'),             # a reset modem lists nothing
                 ('+CGACT:11,1\\nOK', 'inactive'), ('+CGACT:1,10\\nOK', 'inactive'),
                 ('ERROR', 'unknown'), ('+CME ERROR: 100', 'unknown'), ('none', 'unknown')]
        for answer, want in cases:
            with self.subTest(answer=answer):
                r, _ = self.lib('watch_context', modem=self.WATCH_MODEM, CGACT=answer)
                self.assertEqual(r.stdout.strip(), want, r.stderr)

    def test_watch_leaves_the_bearer_alone_while_the_modem_does_not_answer(self):
        """A channel without an answer for up to three rounds: no radio check, no reconnect."""
        r, sent = self.watch(3, CGACT='none')
        self.assertEqual(sent, ['AT+CGACT?'] * 3, r.stderr)
        # ERROR is no answer about the context either
        r, sent = self.watch(3, CGACT='ERROR')
        self.assertEqual(sent, ['AT+CGACT?'] * 3, r.stderr)

    def test_watch_still_reconnects_a_modem_that_stays_silent(self):
        """Four silent rounds make the first failed round, the fifth the reconnect: about 2.5 min instead of 1."""
        r, sent = self.watch(5, CGACT='none')
        self.assertEqual(sent.count('UP'), 1, sent)
        self.assertEqual(sent.index('UP'), len(sent) - 1, sent)
        self.assertEqual(sent.count('AT+CGACT?'), 5, sent)
        self.assertIn('no answer about the data context for 4 rounds', r.stderr)
        # netifd: ifup, as before
        r, sent = self.watch(5, CGACT='none', MU300_NETIFD='1')
        self.assertIn('IFUP wan', sent)

    def test_an_answer_between_silent_rounds_starts_the_count_over(self):
        r, sent = self.watch(1, CGACT='none')
        self.assertNotIn('UP', sent)
        # silent, silent, silent, answered and up, silent x3: never a reconnect
        script = self.tmp / 'answers'
        script.write_text('none\nnone\nnone\nup\nnone\nnone\nnone\n')
        modem = self.WATCH_MODEM.replace(
            "[ \"${CGACT:-}\" != none ] || return 1",
            'n=$(($(cat "$STUBLOG/n" 2>/dev/null || echo 0) + 1)); echo $n > "$STUBLOG/n"\n'
            '            [ "$(sed -n "${n}p" "$STUBLOG/answers")" != none ] || return 1')
        self.stub('ip', 'echo "2: sipa_eth0    inet 10.1.2.3/24 scope global sipa_eth0"')
        r, sent = self.lib('watch', modem=modem, MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0',
                           MU300_WATCH_ROUNDS='7')
        sent = [s.split(' ', 1)[1] for s in sent]
        self.assertNotIn('UP', sent, r.stderr)
        self.assertNotIn('RADIO_ON', sent, r.stderr)

    def test_an_answered_lost_context_still_reconnects_in_two_rounds(self):
        """What a firmware reset leaves (no context listed, or <cid>,0) is evidence: the old two rounds."""
        for answer in ('OK', '+CGACT:1,0\\nOK'):
            with self.subTest(answer=answer):
                r, sent = self.watch(2, CGACT=answer)
                self.assertEqual(sent.count('UP'), 1, sent)
        # active, but the interface lost its address: the same
        r, sent = self.watch(2, NOADDR='1')
        self.assertEqual(sent.count('UP'), 1, sent)
        # active with the address: nothing
        r, sent = self.watch(2)
        self.assertNotIn('UP', sent)

    # FINDINGS 176: a context that is up with the right address, and no downlink. The interface's counters live in
    # $MU300_SYS_NET; every AT+CGACT? (one per round) adds $TXSTEP sent and $RXSTEP received packets. bounded (the
    # radio off and on) and the reboot only log.
    def dl_watch(self, rounds, rx=0, tx=0, **env):
        stats = self.tmp / 'sysnet' / 'sipa_eth0' / 'statistics'
        stats.mkdir(parents=True, exist_ok=True)
        (stats / 'rx_packets').write_text(f'{rx}\n')
        (stats / 'tx_packets').write_text(f'{tx}\n')
        (stats / 'tx_errors').write_text('0\n')
        modem = self.WATCH_MODEM.replace(
            "        'AT+CGACT?')\n",
            "        'AT+CGACT?')\n"
            "            s=$MU300_SYS_NET/sipa_eth0/statistics\n"
            "            echo $(( $(cat $s/tx_packets) + ${TXSTEP:-10} )) > $s/tx_packets\n"
            "            q=$(( $(cat $STUBLOG/q 2>/dev/null || echo 0) + 1 )); echo $q > $STUBLOG/q\n"
            "            [ -z \"${RXFROM:-}\" ] || [ $q -lt \"$RXFROM\" ] || RXSTEP=5\n"
            "            echo $(( $(cat $s/rx_packets) + ${RXSTEP:-0} )) > $s/rx_packets\n") + \
            'bounded() { at "BOUNDED $*"; }\n'
        (self.tmp / 'q').unlink(missing_ok=True)
        self.stub('ip', '[ -n "$NOADDR" ] || echo "2: sipa_eth0    inet 10.1.2.3/24 scope global sipa_eth0"')
        self.stub('ifup', 'echo "$SECONDS IFUP $*" >> "$STUBLOG/at"')
        self.stub('fakereboot', 'echo "$SECONDS REBOOT" >> "$STUBLOG/at"')
        self.stub('fakevpn', 'case $1 in enabled) echo "${VPN_ON:-0}" ;; restart) echo "$SECONDS VPN_RESTART" >> "$STUBLOG/at" ;; esac')
        env.setdefault('MU300_VPN_CMD', 'fakevpn')
        env.setdefault('MU300_DL_STALL_SECS', '0')
        env.setdefault('MU300_DL_REBOOTS', str(self.tmp / 'reboots'))
        r, sent = self.lib('watch', modem=modem, MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0',
                           MU300_WATCH_ROUNDS=str(rounds), MU300_SYS_NET=self.tmp / 'sysnet',
                           MU300_REBOOT_CMD='fakereboot', NOADDR='', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r, [s.split(' ', 1)[1] for s in sent]

    @staticmethod
    def dl_steps(sent):
        return [c for c in sent if c in ('VPN_RESTART', 'IFUP wan', 'UP', 'BOUNDED suspend off', 'BOUNDED resume', 'REBOOT',
                                         'AT+CGACT=0,1')]

    def test_a_stalled_downlink_climbs_the_steps_under_netifd(self):
        """Sent and nothing received, round after round: the VPN again (when one is on), the context, the radio,
        then (nothing received in this whole boot) the reboot, each once."""
        r, sent = self.dl_watch(14, MU300_NETIFD='1', VPN_ON='1')
        self.assertEqual(self.dl_steps(sent), ['VPN_RESTART', 'IFUP wan', 'BOUNDED suspend off', 'BOUNDED resume',
                                               'REBOOT'], r.stderr)
        self.assertIn('step 1: the VPN again', r.stderr)
        self.assertIn('after every step', r.stderr)
        # without a VPN the first step is the context
        (self.tmp / 'reboots').unlink()
        r, sent = self.dl_watch(12, MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), ['IFUP wan', 'BOUNDED suspend off', 'BOUNDED resume', 'REBOOT'],
                         r.stderr)
        self.assertIn('downlink stalled: sipa_eth0 sent', r.stderr)
        self.assertIn('step 2: the data context again', r.stderr)
        self.assertIn('step 4: rebooting (reboot 1 of at most 3 in a row', r.stderr)
        self.assertEqual((self.tmp / 'reboots').read_text().strip(), '1')
        # the evidence went with it: the context, its address and the counters at each step
        log = (self.tmp / 'reboots.log').read_text()
        self.assertIn('sipa_eth0 rx_packets 0', log)
        self.assertIn('+CGPADDR: 1,"10.1.2.3"', log)
        # then the backoff: nothing more in this run
        self.assertIn('the next round of steps in 600 s', r.stderr)

    def test_a_stalled_downlink_on_ubuntu_takes_the_context_down_and_dials(self):
        r, sent = self.dl_watch(3)
        self.assertEqual(self.dl_steps(sent)[:2], ['AT+CGACT=0,1', 'UP'], r.stderr)

    def test_a_working_or_idle_downlink_is_left_alone(self):
        # packets come back: nothing
        r, sent = self.dl_watch(10, RXSTEP='3', MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), [], r.stderr)
        # nothing leaves and nothing comes: an idle link, not a dead one
        r, sent = self.dl_watch(10, TXSTEP='0', MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), [], r.stderr)
        # a packet a round is a downlink too: the third one is past the allowance (two, for a stray router
        # advertisement) before 20 packets have left, and the reference moves on
        r, sent = self.dl_watch(10, RXSTEP='1', TXSTEP='7', MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), [], r.stderr)
        # under the threshold: 20 packets sent in the window are needed
        r, sent = self.dl_watch(1, TXSTEP='5', MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), [], r.stderr)

    def test_the_stall_needs_its_time(self):
        r, sent = self.dl_watch(6, MU300_NETIFD='1', MU300_DL_STALL_SECS='3600')
        self.assertEqual(self.dl_steps(sent), [], r.stderr)

    def test_no_reboot_after_a_boot_that_had_a_downlink_or_when_switched_off(self):
        # 5000 packets received earlier in this boot: the far end may be gone (a VPN server); steps 1 and 2 only
        r, sent = self.dl_watch(12, rx=5000, MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), ['IFUP wan', 'BOUNDED suspend off', 'BOUNDED resume'], r.stderr)
        self.assertIn('no reboot: sipa_eth0 has had a downlink in this boot (5000 packets received)', r.stderr)
        self.assertFalse((self.tmp / 'reboots').exists())
        # the setting: off
        r, sent = self.dl_watch(12, MU300_NETIFD='1', MU300_DL_REBOOT='0')
        self.assertEqual(self.dl_steps(sent), ['IFUP wan', 'BOUNDED suspend off', 'BOUNDED resume'], r.stderr)
        self.assertIn('step 4 (a reboot) is switched off', r.stderr)
        # three reboots in a row already: no fourth
        (self.tmp / 'reboots').write_text('3\n')
        r, sent = self.dl_watch(12, MU300_NETIFD='1')
        self.assertNotIn('REBOOT', sent, r.stderr)
        self.assertIn('no reboot: 3 reboots in a row', r.stderr)

    def test_a_managed_dual_sim_radio_is_not_switched_off_and_on(self):
        (self.tmp / 'run' / 'mu300').mkdir(parents=True, exist_ok=True)
        (self.tmp / 'run' / 'mu300' / 'sim-managed').touch()
        r, sent = self.dl_watch(12, rx=5000, MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), ['IFUP wan'], r.stderr)
        self.assertIn('step 3 (the radio off and on) is not possible on this dual-SIM radio', r.stderr)
        self.assertIn('the next round of steps in 600 s', r.stderr)

    def test_the_reboot_setting_is_read_from_openwrt_and_ubuntu(self):
        self.stub('uci', 'echo "${UCI_DL:-}"')
        for env, want in (({'MU300_NETIFD': '1', 'UCI_DL': '0'}, '0'), ({'MU300_NETIFD': '1'}, '1')):
            r, _ = self.lib('dl_reboot_setting', modem=self.WATCH_MODEM, **env)
            self.assertEqual(r.stdout.strip(), want, (env, r.stderr))
        conf = self.tmp / 'md.conf'
        conf.write_text('MU300_DL_REBOOT=0\n')
        r, _ = self.lib('dl_reboot_setting', modem=self.WATCH_MODEM, MU300_NETIFD='0', MU300_CONF=conf)
        self.assertEqual(r.stdout.strip(), '0', r.stderr)

    def test_a_boot_with_a_downlink_ends_the_count_of_reboots(self):
        (self.tmp / 'reboots').write_text('2\n')
        r, sent = self.dl_watch(2, rx=1000, RXSTEP='5', MU300_NETIFD='1')
        self.assertFalse((self.tmp / 'reboots').exists(), r.stderr)
        # a few packets are not a downlink: the count stays
        (self.tmp / 'reboots').write_text('2\n')
        r, sent = self.dl_watch(2, rx=10, RXSTEP='5', MU300_NETIFD='1')
        self.assertTrue((self.tmp / 'reboots').exists(), r.stderr)

    def test_the_downlink_back_after_a_step_is_said_and_the_steps_start_over(self):
        # stalled for three rounds (step 1 in the third), packets from the fourth round on: no step 2
        r, sent = self.dl_watch(8, RXFROM='4', MU300_NETIFD='1')
        self.assertEqual(self.dl_steps(sent), ['IFUP wan'], r.stderr)
        self.assertIn('the downlink is back: sipa_eth0 received 5 packets (after step 2)', r.stderr)
        # with a VPN on, its restart is the step that brings it back
        r, sent = self.dl_watch(8, RXFROM='4', MU300_NETIFD='1', VPN_ON='1')
        self.assertEqual(self.dl_steps(sent), ['VPN_RESTART'], r.stderr)
        self.assertIn('(after step 1)', r.stderr)

    # 2026-10-07: `mobile-data suspend off` after `suspend lte` never returned on the U30 Air, and the device stayed dark
    # for hours. suspend/resume now end within MU300_SUSPEND_DEADLINE whatever the modem does. Here the real at() talks
    # to an nr2 daemon whose client never answers (a mu300-at that sleeps): the whole command must come back in time,
    # non-zero, every process it started gone, and the files must hand the modem back to the watcher.
    def silent_modem(self):
        hang = self.tmp / 'hang'
        hang.mkdir(exist_ok=True)
        (hang / 'mu300-at').write_text('#!/bin/sh\necho "$* lockwait=$MU300_AT_LOCK_WAIT budget=$MU300_AT_BUDGET" >> "$STUBLOG/hangat"\n'
                                       'echo $$ >> "$STUBLOG/hung.pids"\nexec sleep 1000\n')
        (hang / 'mu300-at').chmod(0o755)
        (self.tmp / 'run' / 'mu300-at2').mkdir(parents=True, exist_ok=True)
        if not (self.tmp / 'run' / 'mu300-at2' / 'cmd').exists():
            os.mkfifo(self.tmp / 'run' / 'mu300-at2' / 'cmd')
        return 'unset -f at; . "$STUBLOG/mobile-data.lib"; PATH="$STUBLOG/hang:$PATH"\n'

    def assert_all_killed(self):
        import time
        pids = [int(p) for p in (self.tmp / 'hung.pids').read_text().split()]
        for pid in pids:
            # a killed process the system has not reaped yet is a zombie: os.kill(pid, 0) still finds it (the macOS
            # runner was slow to reap it, and this failed with "still runs" while nothing ran)
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                st = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
                if not st or st.startswith('Z'):
                    break
                time.sleep(0.1)
            else:
                self.fail(f'pid {pid} still runs')

    def test_suspend_and_resume_end_in_time_with_a_modem_that_never_answers(self):
        cases = [('', 'suspend off', {}),
                 ('lte', 'suspend off', {}),                 # the incident: suspend lte, then suspend off
                 ('', 'suspend lte', {}),
                 ('off', 'resume', {}),
                 ('lte', 'resume', {}),
                 ('off', 'resume nodial', {}),
                 ('', 'suspend off', {'MU300_NETIFD': '1'})]
        for before, cmd, env in cases:
            with self.subTest(before=before, cmd=cmd, **env):
                run = self.tmp / 'run'
                for f in ('mu300-mobile-data.suspend', 'mu300-mobile-data-down', 'mu300-mobile-data.endc-restore',
                          'hung.pids', 'hangat'):
                    (run / f).unlink(missing_ok=True); (self.tmp / f).unlink(missing_ok=True)
                code = self.silent_modem()
                if before:
                    (run / 'mu300-mobile-data.suspend').write_text(before + '\n')
                if 'netifd' in str(env) or env:
                    self.stub('ifdown', 'exit 0'); self.stub('ifup', 'exit 0')
                r, _ = self.lib(code + f'SECONDS=0; rc=0; bounded {cmd} || rc=$?; echo "rc=$rc took=$SECONDS"',
                                MU300_SUSPEND_DEADLINE='3', MU300_MD_STEP_DEADLINE='2', MU300_CFUN_WAIT='0',
                                MU300_URC_LOG=str(self.tmp / 'urc.log'), **env)
                m = re.search(r'rc=(\d+) took=(\d+)', r.stdout)
                self.assertTrue(m, r.stdout + r.stderr)
                self.assertNotEqual(m.group(1), '0', r.stderr)
                self.assertLessEqual(int(m.group(2)), 8, r.stderr)
                self.assertIn('the modem is left to the watcher', r.stderr)
                # consistent: no mode file and no "down" flag, so the watcher restores whatever is off
                self.assertFalse((run / 'mu300-mobile-data.suspend').exists())
                self.assertFalse((run / 'mu300-mobile-data-down').exists())
                # EN-DC may be off by our hand after a failed lte step: noted, so a later resume switches it back on
                self.assertEqual((run / 'mu300-mobile-data.endc-restore').exists(), 'lte' in (before, cmd.split()[-1]))
                self.assert_all_killed()
                # every AT went through mu300-at with a short timeout, client-lock wait and budget
                for line in (self.tmp / 'hangat').read_text().splitlines():
                    m = re.match(r'-t (\d+) .* lockwait=(\d+) budget=(\d+)$', line)
                    self.assertTrue(m, line)
                    self.assertLessEqual(int(m.group(1)), 20, line)
                    self.assertLessEqual(int(m.group(2)), 10, line)
                    self.assertLessEqual(int(m.group(3)), 22, line)
                    self.assertGreaterEqual(int(m.group(3)), int(m.group(1)) + 2, line)   # budget >= T + 2

    def test_endc_left_off_by_a_failed_lte_step_is_restored_by_the_next_resume(self):
        run = self.tmp / 'run'
        run.mkdir(exist_ok=True)
        marker = run / 'mu300-mobile-data.endc-restore'
        # the modem says ERROR: the marker stays and resume fails, so mu300-power tries again
        marker.touch()
        err = self.MODEM.replace("        *) printf 'OK\\n' ;;", "        'AT+SPENDC=1') printf 'ERROR\\n' ;;\n        *) printf 'OK\\n' ;;")
        r, sent = self.lib(self.SUSPEND_STUBS + 'rc=0; resume || rc=$?; echo rc=$rc', modem=err, MU300_AT_DEV='/dev/null')
        self.assertIn('rc=1', r.stdout, r.stderr)
        self.assertTrue(marker.exists())
        # OK: EN-DC back on, the marker gone; no mode file was needed for it
        r, sent = self.suspend_lib('rc=0; resume || rc=$?; echo rc=$rc')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertEqual([c.split(' ', 1)[1] for c in sent], ['AT+SPENDC=1'])
        self.assertFalse(marker.exists())
        # the watcher does not stand aside for it
        marker.touch()
        r, sent = self.lib('watch', MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0', MU300_WATCH_ROUNDS='1')
        self.assertTrue(sent, r.stderr)

    def test_an_error_to_endc_on_leaves_the_restore_marker(self):
        """Re-review D4: mu300-at exits 0 on ERROR. A resume lte whose AT+SPENDC=1 is answered ERROR must leave the
        restore marker (the files go, the watcher keeps working) and fail, so the next resume tries again."""
        run = self.tmp / 'run'
        run.mkdir(exist_ok=True)
        stubdir = self.tmp / 'errat'
        stubdir.mkdir(exist_ok=True)
        (stubdir / 'mu300-at').write_text('#!/bin/sh\n[ "$1" = -t ] && shift 2\necho "$1" >> "$STUBLOG/errat.log"\n'
                                          'case $1 in AT+SPENDC=1) echo ERROR ;; esac\nexit 0\n')
        (stubdir / 'mu300-at').chmod(0o755)
        (run / 'mu300-at2').mkdir(parents=True, exist_ok=True)
        if not (run / 'mu300-at2' / 'cmd').exists():
            os.mkfifo(run / 'mu300-at2' / 'cmd')
        (run / 'mu300-mobile-data.suspend').write_text('lte\n')
        r, _ = self.lib('unset -f at; . "$STUBLOG/mobile-data.lib"; PATH="$STUBLOG/errat:$PATH"\n'
                        'rc=0; bounded resume || rc=$?; echo rc=$rc', MU300_URC_LOG=str(self.tmp / 'urc.log'))
        self.assertNotIn('rc=0', r.stdout, r.stderr)
        self.assertIn('AT+SPENDC=1', (self.tmp / 'errat.log').read_text())
        self.assertTrue((run / 'mu300-mobile-data.endc-restore').exists())
        self.assertFalse((run / 'mu300-mobile-data.suspend').exists())

    def test_a_failed_suspend_keeps_a_down_flag_that_was_there_before(self):
        run = self.tmp / 'run'
        run.mkdir(exist_ok=True)
        (run / 'mu300-mobile-data-down').touch()   # `mobile-data down` by hand: the watcher stays aside
        code = self.silent_modem()
        r, _ = self.lib(code + 'rc=0; bounded suspend off || rc=$?; echo rc=$rc', MU300_SUSPEND_DEADLINE='3',
                        MU300_MD_STEP_DEADLINE='1', MU300_URC_LOG=str(self.tmp / 'urc.log'))
        self.assertNotIn('rc=0', r.stdout)
        self.assertTrue((run / 'mu300-mobile-data-down').exists())
        self.assertFalse((run / 'mu300-mobile-data.suspend').exists())

    def test_bounded_suspend_succeeds_and_ignores_a_hangup(self):
        """The command outlives the terminal that started it (an SSH session over the Wi-Fi it took down)."""
        r, sent = self.suspend_lib('( sleep 0.3; kill -HUP $$ ) & at() { sleep 1; echo "$SECONDS $1" >> "$STUBLOG/at"; echo OK; }\n'
                                   'rc=0; bounded suspend off || rc=$?; echo "rc=$rc"')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertIn('modem suspended (off)', r.stderr)
        self.assertEqual((self.tmp / 'run' / 'mu300-mobile-data.suspend').read_text().strip(), 'off')

    def test_the_commands_go_through_the_deadline(self):
        text = (BIN / 'mobile-data').read_text()
        case = text[text.index('\ncase "${1:-status}" in'):]
        self.assertIn('suspend) bounded suspend', case)
        self.assertIn('bounded resume', case)

    def test_radio_on_subcommand(self):
        """K66: `mobile-data radio-on` switches the radio on and asks for the IMS bearer (K58), nothing more."""
        text = (BIN / 'mobile-data').read_text()
        case = text[text.index('\ncase "${1:-status}" in'):]
        self.assertRegex(case, r'\n    radio-on\)')
        r, sent, cmds = self.radio('main() { set -- radio-on' + case + '\n}\nmain; echo ok',
                                   MU300_AT_DEV='/dev/null')
        self.assertIn('ok', r.stdout, r.stderr)
        self.assertEqual(cmds, ['AT+SMMSWAP=0', 'AT+CFUN?', 'AT+SFUN=4', 'AT+CFUN?', 'AT+CAVIMS=1'])

    def test_background_at_owns_the_lock_itself(self):
        """R30: at() on the direct path (no daemon) in a background subshell writes its own pid into the AT lock, not
        the parent's: the lock owner has to be the process that is reading the channel."""
        r, _ = self.lib('unset -f at; . "$STUBLOG/mobile-data.lib"\n'
                        '( at AT 2 >/dev/null ) & bg=$!\n'
                        f'sleep 0.5; echo "owner=$(cat "{self.tmp}/run/mu300-at/owner/pid") bg=$bg parent=$$"; wait',
                        MU300_AT_DEV='/dev/null')
        m = re.search(r'owner=(\d+) bg=(\d+) parent=(\d+)', r.stdout)
        self.assertTrue(m, r.stdout + r.stderr)
        self.assertEqual(m.group(1), m.group(2))
        self.assertNotEqual(m.group(1), m.group(3))



class SimVoice(ShellTest):
    """VoLTE and VoNR per card (FINDINGS 184, after kanoqwq 5f4732e/53ef7b1): `mobile-data voice`, the saved choices
    and their replay at every bring-up. mobile-data sourced as in MobileData; the modem keeps the two settings in
    $STUBLOG/m-volte and m-vonr, answers $VOICE_REFUSE ("volte"/"vonr": that set is ignored) and says nothing when
    $VOICE_SILENT is set."""
    lib = MobileData.lib
    MODEM = r"""
at() {
    echo "$SECONDS $1" >> "$STUBLOG/at"
    [ -z "${VOICE_SILENT:-}" ] || return 1
    case $1 in
        AT) printf 'OK\n' ;;
        'AT+CAVIMS?') printf '+CAVIMS: %s\nOK\n' "$(cat "$STUBLOG/m-volte" 2>/dev/null || echo 1)" ;;
        AT+CAVIMS=[01]) [ "${VOICE_REFUSE:-}" = volte ] || echo "${1#AT+CAVIMS=}" > "$STUBLOG/m-volte"; printf 'OK\n' ;;
        'AT+SP5GCMDS="get nr synch_param",42') printf '+SP5GCMDS: "get nr synch_param",42,%s\nOK\n' "$(cat "$STUBLOG/m-vonr" 2>/dev/null || echo 1)" ;;
        'AT+SP5GCMDS="set nr param",45,'[01]) [ "${VOICE_REFUSE:-}" = vonr ] || echo "${1##*,}" > "$STUBLOG/m-vonr"; printf 'OK\n' ;;
        *) printf 'OK\n' ;;
    esac
}
"""

    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'sim-voice'

    def run_md(self, code, **env):
        e = dict(MU300_VOICE_CONF=self.conf, MU300_AT_DEV='/dev/null')
        e.update(env)
        # the stack is there and the channel answers (modem_stack, tty_setup)
        return self.lib('modem_stack() { :; }; tty_setup() { :; }\n' + code, **e)

    def sent(self, sent):
        return [s.split(' ', 1)[1] for s in sent if s.split(' ', 1)[1] != 'AT']

    def test_replay_defaults_and_choices(self):
        # nothing saved: VoLTE on, as every release before; VoNR untouched
        r, sent = self.run_md('voice_replay')
        self.assertEqual(self.sent(sent), ['AT+CAVIMS=1'], r.stderr)
        # the card in use's lines only
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text('volte0=0\nvonr0=1\nvolte1=1\nvonr1=0\n')
        r, sent = self.run_md('voice_replay')
        self.assertEqual(self.sent(sent), ['AT+CAVIMS=0', 'AT+SP5GCMDS="set nr param",45,1',
                                           'AT+SP5GCMDS="get nr synch_param",42'], r.stderr)
        r, sent = self.run_md('SIM_SLOT=1; voice_replay')
        self.assertEqual(self.sent(sent)[:2], ['AT+CAVIMS=1', 'AT+SP5GCMDS="set nr param",45,0'], r.stderr)
        # a value that is neither is no choice
        self.conf.write_text('volte0=yes; reboot\nvonr0=2\n')
        r, sent = self.run_md('voice_replay')
        self.assertEqual(self.sent(sent), ['AT+CAVIMS=1'], r.stderr)

    def test_the_dial_and_the_warmup_replay(self):
        text = (BIN / 'mobile-data').read_text()
        self.assertNotIn("at 'AT+CAVIMS=1'", text)
        # the dial (up_locked), the early warm-up (radio-on) and a radio switched back on (rf_apply, FINDINGS 185)
        self.assertEqual([l.strip() for l in text.splitlines()].count('voice_replay'), 3)

    def test_set_on_the_card_in_use(self):
        r, sent = self.run_md('voice_cmd volte off; voice_cmd vonr off; voice_json --read')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.conf.read_text(), 'volte0=0\nvonr0=0\n')
        self.assertEqual((self.tmp / 'm-volte').read_text().strip(), '0')
        self.assertEqual((self.tmp / 'm-vonr').read_text().strip(), '0')
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {'slot': 0, 'slots': [{'volte': False, 'vonr': False}, {'volte': True, 'vonr': None}],
                               'live': {'volte': False, 'vonr': False}})
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o644)

    def test_a_modem_that_does_not_take_it_saves_nothing(self):
        for kind in ('volte', 'vonr'):
            with self.subTest(kind=kind):
                self.conf.unlink(missing_ok=True)
                r, _ = self.run_md(f'voice_cmd {kind} off', VOICE_REFUSE=kind)
                self.assertEqual(r.returncode, 1)
                self.assertIn('nothing was saved', r.stderr)
                self.assertFalse(self.conf.exists())

    def test_the_other_card_or_a_silent_modem_is_saved_for_later(self):
        r, sent = self.run_md('voice_cmd vonr on --slot 1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sent(sent), [])
        self.assertEqual(self.conf.read_text(), 'vonr1=1\n')
        r, sent = self.run_md('voice_cmd volte off', VOICE_SILENT=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('saved; sent when', r.stdout)
        self.assertEqual(self.conf.read_text(), 'vonr1=1\nvolte0=0\n')
        r, _ = self.run_md('voice_json')
        self.assertEqual(json.loads(r.stdout), {'slot': 0, 'slots': [{'volte': False, 'vonr': None},
                                                                     {'volte': True, 'vonr': True}]})

    def test_arguments(self):
        for args in ('volte maybe', 'ims on', 'volte on --slot 2', 'volte on extra', 'vonr on --slot'):
            with self.subTest(args=args):
                r, sent = self.run_md(f'voice_cmd {args}')
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertFalse(self.conf.exists())


class SimRadio(ShellTest):
    """Each SIM's radio on or off, kept across reboots (FINDINGS 185, after kanoqwq's mu300-sim-radio): the setting
    tool, and mobile-data's radio-on paths asking it (mobile-data sourced as in MobileData). The modem keeps its CFUN in
    $STUBLOG/cfun (0 cold or the stack off by SFUN=5, 1 on, 4 an earlier release's flight mode; FINDINGS 202)."""
    lib = MobileData.lib
    TOOL = BIN / 'mu300-sim-radio'
    MODEM = r"""
at() {
    echo "$SECONDS $1" >> "$STUBLOG/at"
    case $1 in
        'AT+CFUN?') printf '+CFUN: %s\nOK\n' "$(cat "$STUBLOG/cfun" 2>/dev/null || echo 0)" ;;
        'AT+CFUN=4') echo 4 > "$STUBLOG/cfun"; printf 'OK\n' ;;
        'AT+CFUN=1') echo 1 > "$STUBLOG/cfun"; printf 'OK\n' ;;
        'AT+SFUN=5') echo "${T_SFUN5:-0}" > "$STUBLOG/cfun"; printf 'OK\n' ;;
        'AT+SFUN=4') echo 1 > "$STUBLOG/cfun"; printf 'OK\n' ;;
        *) printf 'OK\n' ;;
    esac
}
"""

    def setUp(self):
        super().setUp()
        self.policy = self.tmp / 'etc' / 'sim-radio'
        self.run_dir = self.tmp / 'rundir'
        self.stub('mu300-sim', 'case $1 in active) echo "${T_ACTIVE:-0}" ;; status) echo \'{"slot1_supported": 1}\' ;; esac')
        self.stub('fake-md', 'echo "md $*" >> "$STUBLOG/md"; exit "${T_MD_RC:-0}"')
        self.stub('logger', 'exit 0')

    def tool(self, *args, **env):
        e = dict(MU300_SIM_RADIO=self.policy, MU300_SIM_BIN=self.stubs / 'mu300-sim', MU300_MOBILE_DATA=self.stubs / 'fake-md',
                 MU300_RUN=self.run_dir)
        e.update(env)
        return self.script(self.shell, self.TOOL, *args, **e)

    # --- the setting tool, under every shell
    def test_setting(self):
        for self.shell in self.each_shell():
            self.policy.unlink(missing_ok=True)
            (self.tmp / 'md').unlink(missing_ok=True)
            # no file: both on, as every release before
            self.assertEqual(self.tool('allowed', '0').returncode, 0)
            self.assertEqual(self.tool('allowed', '1').returncode, 0)
            o = json.loads(self.tool('status', '--json').stdout)
            self.assertEqual((o['enabled'], o['all_off'], o['active'], o['slot1_supported'], o['busy']),
                             ([True, True], False, 0, 1, False))
            # the other card: saved, nothing applied
            r = self.tool('set', '1', 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.policy.read_text(), '1 0\n')
            self.assertFalse((self.tmp / 'md').exists())
            self.assertEqual(self.tool('allowed', '1').returncode, 1)
            self.assertEqual(self.tool('allowed').returncode, 0)          # the card in use (slot 0)
            # the card in use: saved and applied
            r = self.tool('set', '0', 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.policy.read_text(), '0 0\n')
            self.assertEqual((self.tmp / 'md').read_text(), 'md rf-apply\n')
            self.assertTrue(json.loads(self.tool('status', '--json').stdout)['all_off'])
            self.assertEqual(self.policy.stat().st_mode & 0o777, 0o644)
            # all on; the card in use is slot 1
            r = self.tool('set', 'all', 'on', T_ACTIVE=1)
            self.assertEqual(self.policy.read_text(), '1 1\n')
            # the radio did not follow: saved all the same, exit 1, the result for the page
            r = self.tool('set', '0', 'off', T_MD_RC=1)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.policy.read_text(), '0 1\n')
            self.assertEqual(json.loads(self.tool('status', '--json').stdout)['result'], 'failed')

    def test_a_broken_file_is_both_on(self):
        for self.shell in self.each_shell():
            for text in ('0', '0 0 0\n', 'x y\n', '', '; reboot\n'):
                self.policy.parent.mkdir(exist_ok=True)
                self.policy.write_text(text)
                r = self.tool('allowed', '0')
                self.assertEqual(r.returncode, 0, text)
                self.assertIn('count as on', r.stderr)

    def test_arguments_and_busy(self):
        for self.shell in self.each_shell():
            self.policy.unlink(missing_ok=True)
            shutil.rmtree(self.run_dir, ignore_errors=True)
            for args in (('set', '2', 'on'), ('set', '0', 'maybe'), ('set', 'all'), ('allowed', '3'), ('nonsense',)):
                self.assertEqual(self.tool(*args).returncode, 2, args)
            self.assertFalse(self.policy.exists())
            # a change running (a live holder of the lock) or a SIM switch: refused, nothing saved
            (self.run_dir / 'sim-radio.lock').mkdir(parents=True)
            (self.run_dir / 'sim-radio.lock' / 'pid').write_text(f'{os.getpid()}\n')
            self.assertEqual(self.tool('set', '0', 'off').returncode, 75)
            self.assertTrue(json.loads(self.tool('status', '--json').stdout)['busy'])
            (self.run_dir / 'sim-radio.lock' / 'pid').write_text('999999\n')    # its holder is gone
            self.assertEqual(self.tool('set', '1', 'off').returncode, 0)
            (self.run_dir / 'sim-switching').touch()
            self.assertEqual(self.tool('set', '1', 'on').returncode, 75)
            self.assertEqual(self.policy.read_text(), '1 0\n')
            (self.run_dir / 'sim-switching').unlink()

    # --- mobile-data (bash)
    def md(self, code, policy=None, cfun=None, **env):
        if policy is not None:
            self.policy.parent.mkdir(exist_ok=True)
            self.policy.write_text(policy)
        (self.tmp / 'cfun').unlink(missing_ok=True)
        if cfun is not None:
            (self.tmp / 'cfun').write_text(cfun)
        e = dict(MU300_SIM_RADIO_BIN=self.TOOL, MU300_SIM_RADIO=self.policy, MU300_SIM_BIN=self.stubs / 'mu300-sim',
                 MU300_RUN=self.run_dir, MU300_URC_LOG=self.tmp / 'none', MU300_CFUN_WAIT=0, MU300_AT_DEV='/dev/null',
                 MU300_PLUGIN_LOCK=self.tmp / 'no-plugin')
        e.update(env)
        r, sent = self.lib('modem_stack() { :; }\n' + code, modem=self.MODEM, **e)
        return r, [x.split(' ', 1)[1] for x in sent]

    def test_radio_on_is_refused_while_off(self):
        (self.tmp / 'run' / 'mu300-ril-handshake').parent.mkdir(exist_ok=True)
        # cold radio: stays at 0, no SFUN; rc 7; the hold marker
        r, cmds = self.md('rc=0; radio_on || rc=$?; echo rc=$rc', policy='0 1\n', cfun='0')
        self.assertIn('rc=7', r.stdout, r.stderr)
        self.assertNotIn('AT+SFUN=4', cmds)
        self.assertEqual((self.tmp / 'cfun').read_text().strip(), '0')
        self.assertTrue((self.tmp / 'run' / 'mu300' / 'sim-radio-held').exists())
        # a radio that is on: its stack off (AT+SFUN=5, read back 0 or 2), never AT+CFUN=4 (FINDINGS 202)
        for off in ('0', '2'):
            r, cmds = self.md('rc=0; radio_on || rc=$?; echo rc=$rc', policy='0 1\n', cfun='1', T_SFUN5=off)
            self.assertIn('rc=7', r.stdout, r.stderr)
            self.assertIn('AT+SFUN=5', cmds)
            self.assertNotIn('AT+CFUN=4', cmds)
            self.assertEqual((self.tmp / 'cfun').read_text().strip(), off)
        # SFUN=5 not read back: a failure is said
        r, cmds = self.md('rc=0; rf_off_locked || rc=$?; echo rc=$rc', policy='0 1\n', cfun='1', T_SFUN5='1')
        self.assertIn('rc=1', r.stdout, r.stderr)
        # off by SFUN=5 (0 or 2) with the setting on: the ordinary bring-up's AT+SFUN=4
        for off in ('0', '2'):
            (self.tmp / 'run' / 'mu300-ril-handshake').touch()
            r, cmds = self.md('rc=0; radio_on || rc=$?; echo rc=$rc', policy='1 0\n', cfun=off)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('AT+SFUN=4', cmds)
        # the other card off does not matter to this one; an earlier release's flight mode with the setting on is
        # switched back on
        r, cmds = self.md('rc=0; radio_on || rc=$?; echo rc=$rc', policy='1 0\n', cfun='4')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertIn('AT+CFUN=1', cmds)
        r, cmds = self.md('rc=0; radio_on || rc=$?; echo rc=$rc', policy='1 0\n', cfun='0')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertIn('AT+SFUN=4', cmds)

    def test_the_dial_and_the_reset(self):
        r, cmds = self.md('rc=0; (up_locked) || rc=$?; echo rc=$rc', policy='0 0\n', MU300_NETIFD=1)
        self.assertIn('rc=7', r.stdout, r.stderr)
        self.assertEqual(cmds, [])
        # Ubuntu: no dial, no failure (the service stays up)
        r, cmds = self.md('rc=0; (up_locked) || rc=$?; echo rc=$rc', policy='0 0\n', MU300_NETIFD=0)
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertIn('radio of SIM1 is off', r.stdout)
        self.assertEqual(cmds, [])
        r, cmds = self.md('rc=0; sim_reset || rc=$?; echo rc=$rc', policy='0 1\n', cfun='1')
        self.assertIn('rc=1', r.stdout)
        self.assertEqual(cmds, [])

    def test_rf_apply(self):
        self.stub('ubus', 'echo "$*" >> "$STUBLOG/ubus"; echo "{ \\"up\\": ${T_UP:-true}, \\"autostart\\": ${T_AUTOSTART:-true}, \\"available\\": ${T_AVAILABLE:-true} }"')
        self.stub('ifup', 'echo "ifup $*" >> "$STUBLOG/ifup"')
        held = self.tmp / 'run' / 'mu300' / 'sim-radio-held'
        # off: the WAN restarted (its dial then ends with SIM_RADIO_DISABLED), the stack off (SFUN=5)
        r, cmds = self.md('rf_apply; echo rc=$?', policy='0 1\n', cfun='1', MU300_NETIFD=1)
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertEqual((self.tmp / 'ifup').read_text(), 'ifup wan\n')
        self.assertEqual((self.tmp / 'cfun').read_text().strip(), '0')
        self.assertTrue(held.exists())
        # on: AT+CFUN=1, and the WAN mu300cell made unavailable is made available again (netifd starts it then,
        # unless somebody took it down)
        (self.tmp / 'ifup').unlink(missing_ok=True)
        r, cmds = self.md('rf_apply; echo rc=$?', policy='1 1\n', cfun='4', MU300_NETIFD=1, T_AVAILABLE='false')
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertIn('AT+CFUN=1', cmds)
        self.assertIn('AT+CAVIMS=1', cmds)                     # the card's voice choice, as at every start
        self.assertFalse((self.tmp / 'ifup').exists())
        self.assertIn('notify_proto {"action":5,"available":true}', (self.tmp / 'ubus').read_text())
        self.assertFalse(held.exists())
        # off with the WAN down on purpose (netifd: not up, autostart off): nothing held, no restart
        (self.tmp / 'ifup').unlink(missing_ok=True)
        r, cmds = self.md('rf_apply; echo rc=$?', policy='0 1\n', cfun='1', MU300_NETIFD=1, T_AUTOSTART='false',
                          T_UP='false')
        self.assertFalse(held.exists())
        self.assertFalse((self.tmp / 'ifup').exists())
        self.assertEqual((self.tmp / 'cfun').read_text().strip(), '0')
        # on, and the WAN is available (nothing was made unavailable): nothing asked of netifd
        (self.tmp / 'ubus').unlink(missing_ok=True)
        r, cmds = self.md('rf_apply; echo rc=$?', policy='1 1\n', cfun='4', MU300_NETIFD=1)
        self.assertFalse((self.tmp / 'ifup').exists())
        self.assertNotIn('notify_proto', (self.tmp / 'ubus').read_text())

    def test_every_radio_on_path_asks(self):
        text = (BIN / 'mobile-data').read_text()
        for marker in ('radio_on_locked() {\n    if ! rf_allowed', 'sim_reset() {\n    rf_allowed',
                       'if ! rf_allowed; then continue; fi'):
            self.assertIn(marker, text)
        up = text[text.index('up_locked() {'):text.index('\n}\n', text.index('up_locked() {'))]
        self.assertLess(up.index('rf_allowed'), up.index('radio_on'))
        for name in ('mu300-sim-u30', 'mu300-sim2-start', 'mu300-sim2-warmup'):
            self.assertIn('allowed', (BIN / name).read_text(), name)
        self.assertIn('mu300-sim-radio', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())


class SimState(ShellTest):
    """A slot with no card (FINDINGS 186, after kanoqwq's SIM-state events f59a296/ab26e79): mu300-sim-state's check and
    event filter, mobile-data's dial stopping on an empty slot, mu300-atd's hook."""
    TOOL = BIN / 'mu300-sim-state'

    def setUp(self):
        super().setUp()
        self.run_dir = self.tmp / 'rundir'
        # the card's answer to AT+CPIN? from $STUBLOG/cpin
        self.stub('fake-at', 'echo "$*" >> "$STUBLOG/at"; cat "$STUBLOG/cpin"')
        self.stub('mu300-sim', 'echo "${T_ACTIVE:-0}"')
        self.stub('logger', 'exit 0')
        self.stub('setsid', 'echo "setsid $*" >> "$STUBLOG/setsid"')

    def tool(self, *args, **env):
        e = dict(MU300_RUN=self.run_dir, MU300_AT_BIN=self.stubs / 'fake-at', MU300_SIM_BIN=self.stubs / 'mu300-sim',
                 MU300_MOBILE_DATA='/md')
        e.update(env)
        return self.script(self.shell, self.TOOL, *args, **e)

    def test_check(self):
        cases = [('+CPIN: READY\nOK\n', 'ready'), ('+CPIN: SIM PIN\nOK\n', 'locked'), ('+CPIN: SIM PUK\nOK\n', 'locked'),
                 ('+CME ERROR: 10\n', 'absent'), ('+CPIN: NOT INSERTED\nOK\n', 'absent'),
                 ('+CME ERROR: SIM not inserted\n', 'absent')]
        for self.shell in self.each_shell():
            for answer, want in cases:
                with self.subTest(answer=answer):
                    shutil.rmtree(self.run_dir, ignore_errors=True)
                    (self.tmp / 'cpin').write_text(answer)
                    r = self.tool('check')
                    self.assertEqual(r.stdout.strip(), want, r.stderr)
                    self.assertEqual(self.tool('get').stdout.strip(), want)
            # an answer that says nothing about the card (busy, no answer) keeps what was known
            for answer in ('+CME ERROR: 14\n', '', 'mu300-at: busy\n'):
                (self.tmp / 'cpin').write_text(answer)
                self.assertEqual(self.tool('check').stdout.strip(), 'absent')
            # the other card is never asked
            (self.tmp / 'at').unlink(missing_ok=True)
            self.assertEqual(self.tool('check', '1').stdout, '')
            self.assertFalse((self.tmp / 'at').exists())
            self.assertEqual(self.tool('check', '7').returncode, 2)

    def test_a_card_back_dials_again(self):
        for self.shell in self.each_shell():
            shutil.rmtree(self.run_dir, ignore_errors=True)
            (self.tmp / 'setsid').unlink(missing_ok=True)
            self.run_dir.mkdir()
            (self.run_dir / 'sim-missing-held').write_text('0\n')
            (self.tmp / 'cpin').write_text('+CME ERROR: 10\n')
            self.tool('check')
            self.assertTrue((self.run_dir / 'sim-missing-held').exists())
            self.assertFalse((self.tmp / 'setsid').exists())
            (self.tmp / 'cpin').write_text('+CPIN: READY\nOK\n')
            self.assertEqual(self.tool('check').stdout.strip(), 'ready')
            self.assertFalse((self.run_dir / 'sim-missing-held').exists())
            for _ in range(50):
                if (self.tmp / 'setsid').exists():
                    break
                time.sleep(0.1)
            self.assertEqual((self.tmp / 'setsid').read_text(), 'setsid /md up\n')
            # OpenWrt: no held marker needed (netifd's teardown runs `down`); a card back after "absent" makes the
            # WAN mu300cell made unavailable available again
            root = self.tmp / 'root'
            (root / 'etc').mkdir(parents=True, exist_ok=True)
            (root / 'etc' / 'openwrt_release').write_text('')
            self.stub('ubus', 'echo "$*" >> "$STUBLOG/ubus"; echo "{ \\"available\\": false }"')
            (self.tmp / 'ubus').unlink(missing_ok=True)
            (self.tmp / 'cpin').write_text('+CME ERROR: 10\n')
            self.tool('check', MU300_SYSROOT=root)
            self.assertFalse((self.tmp / 'ubus').exists())
            (self.tmp / 'cpin').write_text('+CPIN: READY\nOK\n')
            self.tool('check', MU300_SYSROOT=root)
            self.assertIn('notify_proto {"action":5,"available":true}', (self.tmp / 'ubus').read_text())

    def test_event_filter(self):
        # numbers after the cause are the RIL's to ignore: a card coming up says 3,100 and then 3,0,0,1 (kanoqwq's
        # b8d4f222); a field that is not a 32-bit decimal number, or an empty one, is not a hint
        hint = ['+ECIND: 3,0', '+ECIND: 3,2,5', '+ECIND:3,100', '+ECIND: 3,1,34', '+ECIND: 3,4,0', '+ECIND: 3,0,0,1',
                '+ECIND: 3,1,2,0,1', '+ECIND: 3,0,1,2']
        other = ['+ECIND: 1,0', '+ECIND: 3,1,3', '+ECIND: 3,0,x', '+ECIND: 3,9', '+CEREG: 1',
                 '+ECIND: 3,0;reboot', '+ECIND: 3,1,2,7junk', '+ECIND: 3,0,0,-1', '+ECIND: 3,0,0,4294967296',
                 '+ECIND: 3,0,0,', '+ECIND: 3,0,,1', '+ECIND: 3,1,3,0']
        self.shell = self.shells[0]   # the event's check waits 2 s: one shell is enough
        if True:
            for urc in hint + other:
                with self.subTest(urc=urc):
                    shutil.rmtree(self.run_dir, ignore_errors=True)
                    (self.tmp / 'at').unlink(missing_ok=True)
                    (self.tmp / 'cpin').write_text('+CPIN: READY\nOK\n')
                    r = self.tool('event', '0', urc)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    state = self.run_dir / 'sim0.state'
                    for _ in range(60 if urc in hint else 30):   # a check starts 2 s after its event
                        if state.exists():
                            break
                        time.sleep(0.1)
                    self.assertEqual(state.exists(), urc in hint, urc)

    def test_the_dial_stops_on_an_empty_slot(self):
        md = MobileData('test_registered_from_urc')
        md.setUp()
        try:
            st = md.tmp / 'sim-state'
            st.write_text('#!/bin/sh\necho "state $*" >> "$STUBLOG/state"\n[ "$1" = check ] && echo "${T_STATE:-ready}"\nexit 0\n')
            st.chmod(0o755)
            for f in ('nft', 'sysctl', 'ethtool', 'resolvectl', 'logger'):
                md.stub(f, 'exit 0')
            r, sent = md.lib('rc=0; (up_locked) || rc=$?; echo rc=$rc', MU300_NETIFD=1, MU300_SIM_STATE_BIN=st,
                             T_STATE='absent', MU300_AT_DEV='/dev/null', MU300_URC_LOG=md.tmp / 'none', MU300_CFUN_WAIT=0)
            self.assertIn('rc=6', r.stdout, r.stderr)
            self.assertEqual((md.tmp / 'state').read_text().split('\n').count('state check'), 2)
            self.assertFalse([x for x in sent if 'CEREG' in x or 'CGACT' in x], sent)
            self.assertTrue((md.tmp / 'run' / 'mu300' / 'sim-missing-held').exists())
            text = (BIN / 'mobile-data').read_text()
            self.assertIn('rm -f "$RF_HELD" "$SIM_HELD"; down ;;', text)
            atd = (BIN / 'mu300-atd').read_text()
            self.assertIn('"$SIM_STATE" event "$((n / 3))" "$urc"', atd)
            self.assertIn('mu300-sim-state', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())
        finally:
            md.tearDown()

class DataRoaming(ShellTest):
    """Data roaming (FINDINGS 120): the setting, the dial's gate, the watchdog's two moves. mobile-data sourced as in
    MobileData (its lib() and modem); the modem here also answers AT+C5GREG? ($C5GREG) and AT+CGACT? ($CGACT), and
    `uci` is a stub answering network.wan.roaming / roaming_internal from $T_ROAM0 / $T_ROAM1."""
    lib = MobileData.lib
    MODEM = MobileData.MODEM.replace(
        "        'AT+CFUN?')",
        "        'AT+C5GREG?') printf '%s\\nOK\\n' \"${C5GREG:-+C5GREG: 2,0}\" ;;\n"
        "        'AT+CGACT?') printf '%s\\nOK\\n' \"${CGACT:-+CGACT: 1,0}\" ;;\n"
        "        'AT+CFUN?')")
    HOME = '+CEREG: 2,1,"1A2B","0123ABCD",7'
    ROAM = '+CEREG: 2,5,"1A2B","0123ABCD",7'

    def setUp(self):
        super().setUp()
        self.stub('uci', 'echo "uci $*" >> "$STUBLOG/uci"\n'
                  'case "$*" in\n'
                  '  "-q get network.wan.roaming") [ -n "${T_ROAM0:-}" ] && echo "$T_ROAM0" || exit 1 ;;\n'
                  '  "-q get network.wan.roaming_internal") [ -n "${T_ROAM1:-}" ] && echo "$T_ROAM1" || exit 1 ;;\n'
                  'esac')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/logger"')
        self.stub('ifup', 'echo "ifup $*" >> "$STUBLOG/ifup"')
        self.stub('ubus', 'echo "{ \\"up\\": false, \\"autostart\\": ${T_AUTOSTART:-true} }"')
        for n in ('nft', 'sysctl', 'ethtool', 'resolvectl'):
            self.stub(n, 'exit 0')
        # the bearer has the context's address (the watchdog's healthy round)
        self.stub('ip', 'case "$*" in *"-o addr show"*) echo "2: sipa_eth0    inet 10.1.2.3/24 scope global sipa_eth0" ;;\n'
                  '  *"addr show"*) echo "    inet 10.1.2.3/24 scope global sipa_eth0" ;; esac; exit 0')
        self.run_dir = self.tmp / 'run'
        self.held = self.run_dir / 'mu300' / 'data-roaming-held'
        self.conf = self.tmp / 'mobile-data.conf'

    def dial(self, cereg, **env):
        e = dict(MU300_NETIFD=1, MU300_AT_DEV='/dev/null', MU300_URC_LOG=self.tmp / 'none', MU300_CFUN_WAIT=0,
                 CEREG=cereg)
        e.update(env)
        r, sent = self.lib('up_locked', **e)
        return r, [s.split(' ', 1)[1] for s in sent]

    def radio_log(self):
        p = self.run_dir / 'mu300' / 'radio.log'
        return p.read_text() if p.exists() else ''

    def test_the_registration_says_home_roaming_or_nothing(self):
        cases = [('1', '', 'home'), ('5', '', 'roaming'), ('9', '', 'home'), ('10', '', 'roaming'),
                 ('0', '5', 'roaming'), ('2', '1', 'home'), ('2', '', 'unknown'), ('', '', 'unknown'),
                 ('8', '0', 'unknown'), ('3', '', 'unknown')]
        code = '; '.join(f'echo "{a}/{b}=$(roam_classify "{a}" "{b}")"' for a, b, _ in cases)
        r, _ = self.lib(code)
        for a, b, want in cases:
            self.assertIn(f'{a}/{b}={want}\n', r.stdout, r.stderr)
        # the query forms, as the modem answers them; the 5G registration only when LTE's says neither
        r, sent = self.lib('roam_state', CEREG=self.ROAM)
        self.assertEqual(r.stdout.strip(), 'roaming', r.stderr)
        self.assertEqual([s.split(' ', 1)[1] for s in sent], ['AT+CEREG?'])
        r, sent = self.lib('roam_state', CEREG='+CEREG: 2,0', C5GREG='+C5GREG: 2,5,"00A1","0000012345",11')
        self.assertEqual(r.stdout.strip(), 'roaming', r.stderr)
        self.assertEqual([s.split(' ', 1)[1] for s in sent], ['AT+CEREG?', 'AT+C5GREG?'])
        r, _ = self.lib('roam_state', CEREG='ERROR', C5GREG='ERROR')
        self.assertEqual(r.stdout.strip(), 'unknown')

    def test_the_setting_and_its_values(self):
        words = {'1': '1', 'on': '1', 'yes': '1', 'true': '1', '0': '0', 'off': '0', 'no': '0', 'false': '0',
                 '': '', 'maybe': '', '0 ; reboot': '', '2': '', 'ON': ''}
        for v, want in words.items():
            r, _ = self.lib(f'echo "[$(roaming_word {shlex.quote(v)})]"')
            self.assertEqual(r.stdout.strip(), f'[{want}]', v)
        # OpenWrt: one option per card; unset or not a word: allowed
        r, _ = self.lib('echo "$(roaming_setting 0) $(roaming_setting 1)"', MU300_NETIFD=1, T_ROAM0='0', T_ROAM1='1')
        self.assertEqual(r.stdout.strip(), '0 1', r.stderr)
        r, _ = self.lib('echo "$(roaming_setting 0) $(roaming_setting 1)"', MU300_NETIFD=1, T_ROAM1='nonsense')
        self.assertEqual(r.stdout.strip(), '1 1', r.stderr)
        # Ubuntu: the settings file, read (quotes, CR, the last line), never executed
        self.conf.write_text('MU300_APN=internet\nMU300_DATA_ROAMING=1\nMU300_DATA_ROAMING="0"\r\n')
        r, _ = self.lib('roaming_setting', MU300_NETIFD=0, MU300_CONF=self.conf)
        self.assertEqual(r.stdout.strip(), '0', r.stderr)
        self.conf.write_text('MU300_DATA_ROAMING=$(touch "$STUBLOG/ran")\n')
        r, _ = self.lib('roaming_setting', MU300_NETIFD=0, MU300_CONF=self.conf)
        self.assertEqual(r.stdout.strip(), '1', r.stderr)
        self.assertFalse((self.tmp / 'ran').exists())
        # no file: allowed (the default, today's behaviour)
        r, _ = self.lib('roaming_setting', MU300_NETIFD=0, MU300_CONF=self.tmp / 'none.conf')
        self.assertEqual(r.stdout.strip(), '1', r.stderr)

    def test_the_default_dials_a_roaming_sim_as_before(self):
        """Nothing set: a SIM registered as roaming gets data, and no extra question is sent."""
        r, cmds = self.dial(self.ROAM)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('IP=10.1.2.3\n', r.stdout)
        self.assertIn('AT+CGACT=1,1', cmds)
        self.assertEqual(cmds.count('AT+CEREG?'), 1, cmds)
        self.assertNotIn('AT+C5GREG?', cmds)
        self.assertFalse(self.held.exists())

    def test_roaming_off_holds_the_dial_under_netifd(self):
        for _ in range(2):
            r, cmds = self.dial(self.ROAM, T_ROAM0='0')
            self.assertEqual(r.returncode, 4, r.stderr)
            self.assertNotIn('IP=', r.stdout)
            self.assertFalse([c for c in cmds if c.startswith(('AT+CGACT', 'AT+CGDCONT', 'AT+CGDATA', 'sipa-dele'))], cmds)
            self.assertEqual(self.held.read_text().strip(), '0')
            self.assertTrue((self.run_dir / 'mu300-mobile-data-down').exists())
        # said once, not on every retry of netifd
        self.assertEqual(self.radio_log().count('data roaming is off'), 1, self.radio_log())
        self.assertEqual((self.tmp / 'logger').read_text().count('data roaming is off'), 1)
        # at home the same setting dials, and the hold is over
        r, cmds = self.dial(self.HOME, T_ROAM0='0')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('AT+CGACT=1,1', cmds)
        self.assertFalse(self.held.exists())
        self.assertIn('on the home network, mobile data comes back', self.radio_log())

    def test_the_hold_is_said_once_in_the_journal(self):
        """By hand the line goes to stderr too; under systemd stderr is the journal, where logger already put it
        (seen on an F50: the same line twice)."""
        r, _ = self.dial(self.ROAM, T_ROAM0='0', JOURNAL_STREAM='')   # (a CI runner under systemd has one)
        self.assertIn('registered on a roaming network and data roaming is off', r.stderr)
        self.held.unlink()
        r, _ = self.dial(self.ROAM, T_ROAM0='0', JOURNAL_STREAM='8:12345')
        self.assertNotIn('registered on a roaming network', r.stderr)
        self.assertEqual((self.tmp / 'logger').read_text().count('data roaming is off'), 2)

    def test_a_registration_that_cannot_be_read_is_no_dial_with_roaming_off(self):
        # registered according to the URC log, then the queries say nothing
        log = self.tmp / 'urc.log'
        log.write_text('')
        modem = self.MODEM.replace("'AT+CEREG?') printf '%s\\nOK\\n' \"${CEREG:-+CEREG: 2,2}\" ;;",
                                   "'AT+CEREG?') printf 'ERROR\\n' ;;")
        r, sent = self.lib('(sleep 1; echo "+CEREG: 1" >> "$MU300_URC_LOG") & up_locked', modem=modem, MU300_NETIFD=1,
                           MU300_AT_DEV='/dev/null', MU300_URC_LOG=log, MU300_CFUN_WAIT=0, T_ROAM0='0', C5GREG='ERROR')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertFalse([s for s in sent if 'AT+CGACT' in s], sent)
        self.assertFalse(self.held.exists())
        self.assertIn('could not tell', r.stderr)

    def test_the_second_card_has_its_own_setting(self):
        """dual SIM: slot 1 reads roaming_internal; the hold names the slot."""
        r, cmds = self.dial(self.ROAM, MU300_SIM_SLOT='1', T_ROAM0='0', T_ROAM1='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('AT+CGACT=1,1', cmds)
        self.assertIn('uci -q get network.wan.roaming_internal', (self.tmp / 'uci').read_text())
        r, cmds = self.dial(self.ROAM, MU300_SIM_SLOT='1', T_ROAM0='1', T_ROAM1='0')
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.held.read_text().strip(), '1')

    def test_the_card_is_asked_on_its_own_channel(self):
        """FINDINGS 74: each card's dial channel answers for that card; the question carries no AT+SPACTCARD (which
        the panel's terminal refuses as a command anyway)."""
        for d in ('mu300-at2', 'mu300-at5'):
            (self.run_dir / d).mkdir(parents=True, exist_ok=True)
            if not (self.run_dir / d / 'cmd').exists():
                os.mkfifo(self.run_dir / d / 'cmd')
        (self.run_dir / 'mu300-radio-freeze').touch()
        stubdir = self.tmp / 'chan'
        stubdir.mkdir(exist_ok=True)
        (stubdir / 'mu300-at').write_text('#!/bin/sh\n[ "$1" = -t ] && shift 2\n'
                                          'echo "${MU300_AT_DIR##*/} $1" >> "$STUBLOG/chan.log"\n'
                                          'echo "+CEREG: 2,5"; echo OK\n')
        (stubdir / 'mu300-at').chmod(0o755)
        r, _ = self.lib('unset -f at; . "$STUBLOG/mobile-data.lib"; PATH="$STUBLOG/chan:$PATH"; roam_state',
                        MU300_SIM_SLOT='1')
        self.assertEqual(r.stdout.strip().splitlines()[-1], 'roaming', r.stderr)
        self.assertEqual((self.tmp / 'chan.log').read_text().splitlines(), ['mu300-at5 AT+CEREG?'])

    def test_ubuntu_holds_without_a_failure_and_the_watchdog_brings_data_back(self):
        self.conf.write_text('MU300_DATA_ROAMING=0\n')
        env = dict(MU300_NETIFD=0, MU300_CONF=self.conf)
        r, cmds = self.dial(self.ROAM, **env)
        self.assertEqual(r.returncode, 0, r.stderr)          # the service stays up: no restart every 30 s
        self.assertIn('held back', r.stdout)
        self.assertNotIn('AT+CGACT=1,1', cmds)
        self.assertTrue(self.held.exists())
        watch = 'up() { echo up >> "$STUBLOG/ups"; }\nwatch'
        wenv = dict(env, MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0', MU300_WATCH_ROUNDS='3')
        # still roaming: one question a round, no dial, no "mobile data lost"
        r, sent = self.lib(watch, CEREG=self.ROAM, **wenv)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(cmds), {'AT+CEREG?'}, cmds)
        self.assertFalse((self.tmp / 'ups').exists())
        self.assertNotIn('lost', r.stderr)
        # home again: the hold ends and the watchdog dials, once
        r, sent = self.lib(watch, CEREG=self.HOME, **wenv)
        self.assertEqual((self.tmp / 'ups').read_text().split(), ['up'], r.stderr)
        self.assertFalse(self.held.exists())
        # or roaming allowed meanwhile, still abroad
        (self.tmp / 'ups').unlink()
        self.held.write_text('0\n')
        self.conf.write_text('MU300_DATA_ROAMING=1\n')
        r, sent = self.lib(watch, CEREG=self.ROAM, **wenv)
        self.assertEqual((self.tmp / 'ups').read_text().split(), ['up'], r.stderr)
        self.assertIn('data roaming: allowed', self.radio_log())

    def test_the_watchdog_takes_data_down_when_the_card_goes_roaming(self):
        """A live connection, then roaming with data roaming off: down once (no redial loop), and the default does
        not ask more than before."""
        wenv = dict(MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0', MU300_WATCH_ROUNDS='3', CGACT='+CGACT:1,1')
        # netifd: the wan restarted once, its dial then holds; later rounds leave it to netifd
        r, sent = self.lib('watch', MU300_NETIFD=1, CEREG=self.ROAM, T_ROAM0='0', **wenv)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / 'ifup').read_text().splitlines(), ['ifup wan'])
        self.assertTrue(self.held.exists())
        self.assertEqual(self.radio_log().count('data roaming is off'), 1)
        # the wan taken down by hand (netifd's autostart off; its teardown left the marker): the hold ends
        (self.tmp / 'ifup').unlink(missing_ok=True)
        (self.run_dir / 'mu300-mobile-data-down').touch()
        r, _ = self.lib('watch', MU300_NETIFD=1, CEREG=self.ROAM, T_ROAM0='0', T_AUTOSTART='false', **wenv)
        self.assertFalse(self.held.exists())
        self.assertFalse((self.tmp / 'ifup').exists())
        # Ubuntu: the connection goes down as `mobile-data down` takes it
        (self.run_dir / 'mu300-mobile-data-down').unlink()
        self.conf.write_text('MU300_DATA_ROAMING=0\n')
        r, sent = self.lib('up() { echo up >> "$STUBLOG/ups"; }\nwatch', MU300_NETIFD=0, MU300_CONF=self.conf,
                           CEREG=self.ROAM, **wenv)
        cmds = [s.split(' ', 1)[1] for s in sent]
        self.assertEqual(cmds.count('AT+CGACT=0,1'), 1, cmds)
        self.assertTrue(self.held.exists())
        self.assertFalse((self.tmp / 'ups').exists())
        # the default (nothing set), roaming or not: the round asks what it asked before, and nothing goes down
        self.held.unlink()
        (self.run_dir / 'mu300-mobile-data-down').unlink()   # (down's)
        for cereg in (self.ROAM, self.HOME):
            (self.tmp / 'ifup').unlink(missing_ok=True)
            r, sent = self.lib('watch', MU300_NETIFD=1, CEREG=cereg, **dict(wenv, MU300_WATCH_ROUNDS='1'))
            cmds = [s.split(' ', 1)[1] for s in sent]
            self.assertEqual(cmds.count('AT+CEREG?'), 1, cmds)
            self.assertNotIn('AT+C5GREG?', cmds)
            self.assertFalse((self.tmp / 'ifup').exists(), (cmds, r.stderr))
            self.assertFalse(self.held.exists())

    def test_the_watchdog_stands_aside_while_suspended_and_held(self):
        self.held.parent.mkdir(parents=True, exist_ok=True)
        self.held.write_text('0\n')
        (self.run_dir / 'mu300-mobile-data.suspend').write_text('off\n')
        r, sent = self.lib('watch', MU300_NETIFD=0, MU300_AT_DEV='/dev/null', MU300_WATCH_INTERVAL='0',
                           MU300_WATCH_ROUNDS='2', CEREG=self.HOME)
        self.assertEqual(sent, [])
        self.assertTrue(self.held.exists())

    def test_the_test_hook_only_as_a_file_of_ours(self):
        hook = self.run_dir / 'mu300' / 'roaming-test'
        hook.parent.mkdir(parents=True, exist_ok=True)
        r, _ = self.lib('roam_classify 1')
        self.assertEqual(r.stdout.strip(), 'home')
        hook.write_text('home-as-roaming\n')
        r, _ = self.lib('roam_classify 1')
        self.assertEqual(r.stdout.strip(), 'roaming')
        hook.write_text('yes\n')
        r, _ = self.lib('roam_classify 1')
        self.assertEqual(r.stdout.strip(), 'home')
        hook.unlink()
        (self.tmp / 'elsewhere').write_text('home-as-roaming\n')
        hook.symlink_to(self.tmp / 'elsewhere')
        r, _ = self.lib('roam_classify 1')
        self.assertEqual(r.stdout.strip(), 'home')

    def test_the_command_writes_the_setting(self):
        # Ubuntu: the other lines kept (the APN password among them), root's alone, the old value replaced
        self.conf.write_text('MU300_APN=internet\nMU300_APN_PASSWORD=s3cret\nMU300_DATA_ROAMING=1\n')
        self.conf.chmod(0o644)
        r, _ = self.lib('roaming_cmd off; echo rc=$?', MU300_NETIFD=0, MU300_CONF=self.conf)
        self.assertIn('rc=0', r.stdout, r.stderr)
        self.assertEqual(self.conf.read_text(), 'MU300_APN=internet\nMU300_APN_PASSWORD=s3cret\nMU300_DATA_ROAMING=0\n')
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
        r, _ = self.lib('roaming_json', MU300_NETIFD=0, MU300_CONF=self.conf)
        self.assertEqual(json.loads(r.stdout), {'allowed': False, 'held': False, 'slot': 0, 'per_sim': False,
                                                'slots': [False, False]})
        for bad in ('maybe', 'on --slot 1', 'on extra'):
            r, _ = self.lib(f'rc=0; roaming_cmd {bad} || rc=$?; echo rc=$rc', MU300_NETIFD=0, MU300_CONF=self.conf)
            self.assertIn('rc=2', r.stdout, bad)
        self.assertIn('MU300_DATA_ROAMING=0', self.conf.read_text())
        # a file that is not there yet is made, root's alone
        new = self.tmp / 'etc' / 'mobile-data.conf'
        r, _ = self.lib('roaming_cmd on', MU300_NETIFD=0, MU300_CONF=new)
        self.assertEqual(new.read_text(), 'MU300_DATA_ROAMING=1\n', r.stderr)
        self.assertEqual(new.stat().st_mode & 0o777, 0o600)
        # OpenWrt: the card in use, or the one named
        r, _ = self.lib('roaming_cmd off; roaming_cmd on --slot 1; rc=0; roaming_cmd on --slot 2 || rc=$?; echo rc=$rc',
                        MU300_NETIFD=1)
        self.assertIn('rc=2', r.stdout, r.stderr)
        calls = (self.tmp / 'uci').read_text().splitlines()
        self.assertIn('uci -q set network.wan.roaming=0', calls)
        self.assertIn('uci -q set network.wan.roaming_internal=1', calls)
        self.assertEqual(calls.count('uci -q commit network'), 2)
        self.assertFalse([c for c in calls if 'slot' in c or '=2' in c], calls)

    def test_an_explicit_down_ends_the_hold_on_ubuntu_only(self):
        text = (BIN / 'mobile-data').read_text()
        case = text[text.index('\ncase "${1:-status}" in'):]
        for netifd, kept in (('0', False), ('1', True)):
            self.held.parent.mkdir(parents=True, exist_ok=True)
            self.held.write_text('0\n')
            r, _ = self.lib('down() { :; }\nmain() { set -- down' + case + '\n}\nmain; echo ok', MU300_NETIFD=netifd)
            self.assertIn('ok', r.stdout, r.stderr)
            self.assertEqual(self.held.exists(), kept, netifd)


class RadioLocked(ShellTest):
    """mobile-data radio-locked CMD / radio-busy: another program's radio sequence (the LuCI panel's) runs under the
    radio lock radio_on takes, held by mobile-data itself; a held lock is "busy" (75), nothing run (final review
    minor 3). mobile-data is copied (still named mobile-data: lock_owner_alive knows the holder by that name) with
    /run/ in the scratch directory."""

    def setUp(self):
        super().setUp()
        if not shutil.which('bash'):
            self.skipTest('no bash')
        (self.tmp / 'run').mkdir()
        self.md = self.tmp / 'mobile-data'
        self.md.write_text((BIN / 'mobile-data').read_text().replace('/run/', f'{self.tmp}/run/'))
        self.md.chmod(0o755)
        self.link = self.tmp / 'run' / 'mu300-radio-on.owner'

    def run_md(self, *args, **env):
        return subprocess.run(['bash', str(self.md)] + list(args), capture_output=True, text=True, timeout=30,
                              env=self.env(**env))

    def test_runs_the_command_holding_the_lock(self):
        r = self.run_md('radio-locked', 'sh', '-c', f'readlink "{self.link}"; exit 3')
        self.assertEqual(r.returncode, 3, r.stderr)        # the command's own status
        self.assertTrue(r.stdout.strip().isdigit(), r.stdout)
        self.assertFalse(os.path.lexists(self.link))         # dropped again
        self.assertEqual(self.run_md('radio-busy').returncode, 1)

    def test_a_held_lock_is_busy_and_nothing_runs(self):
        holder = subprocess.Popen(['bash', str(self.md), 'radio-locked', 'sleep', '3'], env=self.env())
        try:
            deadline = time.monotonic() + 10
            while not os.path.lexists(self.link) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(os.readlink(self.link), str(holder.pid))
            r = self.run_md('radio-busy')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, str(holder.pid)))
            t0 = time.monotonic()
            r = self.run_md('radio-locked', 'touch', str(self.tmp / 'ran'))
            self.assertEqual(r.returncode, 75, r.stderr)
            self.assertIn('busy', r.stderr)
            self.assertLess(time.monotonic() - t0, 5)         # no wait by default
            self.assertFalse((self.tmp / 'ran').exists())
            # TERM: the holder keeps the lock while its command still runs, and drops it on its way out
            holder.terminate()
            time.sleep(0.5)
            self.assertTrue(os.path.lexists(self.link))
            holder.wait(timeout=10)
            self.assertFalse(os.path.lexists(self.link))
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait()

    def test_a_dead_holder_is_taken_over(self):
        dead = subprocess.Popen(['true'])
        dead.wait()
        os.symlink(str(dead.pid), self.link)
        self.assertEqual(self.run_md('radio-busy').returncode, 1)
        r = self.run_md('radio-locked', 'touch', str(self.tmp / 'ran'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / 'ran').exists())


class SipaDele(ShellTest):
    """K45, K46: extra-modules and sipa-dele-start, copied with /lib/modules, /proc/modules, /opt/mu300/bin and
    /dev/stty_nr1 pointed at the scratch directory (and /dev/null); uname, insmod, modprobe, dmesg, sleep, mu300-at
    and sipa-dele-start are stubs that write what they were asked to $STUBLOG/calls."""

    def setUp(self):
        super().setUp()
        self.mods = self.tmp / 'lib' / 'modules' / '5.4.test'
        self.mods.mkdir(parents=True)
        self.stub('uname', 'echo 5.4.test')
        self.stub('insmod', 'echo "insmod $*" >> "$STUBLOG/calls"\nexit "${INSMOD_RC:-0}"')
        self.stub('modprobe', 'echo "modprobe $*" >> "$STUBLOG/calls"\nexit "${MODPROBE_RC:-0}"')
        # the sbuf lines from the modem loader's start may have left the ring buffer long ago: not asked for
        self.stub('dmesg', 'echo "[  80.1] sipa_dele: channel 5-120 send open msg"')
        self.stub('sleep', ':')
        self.stub('mu300-at', 'echo "mu300-at $*" >> "$STUBLOG/calls"\nprintf "%s\\nOK\\n" "${CGATT:-+CGATT: 1}"')
        self.stub('sipa-dele-start', 'echo "sipa-dele-start" >> "$STUBLOG/calls"')

    def copy(self, name):
        text = (BIN / name).read_text()
        if name == 'sipa-dele-start':
            text = text.replace('/dev/stty_nr$((SIM_SLOT * 3 + 1))', '/dev/null')
        for a, b in (('/lib/modules/', f'{self.tmp}/lib/modules/'), ('/proc/modules', f'{self.tmp}/proc-modules'),
                     ('/opt/mu300/bin/', f'{self.stubs}/'), ('/dev/stty_nr1', '/dev/null')):
            text = text.replace(a, b)
        p = self.tmp / name
        p.write_text(text)
        return p

    def run_script(self, shell, name, modules='', **env):
        (self.tmp / 'proc-modules').write_text(modules)
        (self.tmp / 'calls').unlink(missing_ok=True)
        r = self.script(shell, self.copy(name), **env)
        time.sleep(0.3)   # anything started in the background has written its line by now
        calls = (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else ''
        return r, calls

    def test_extra_modules_never_starts_the_delegate(self):
        """K45: on every system (OpenWrt's flat modules and Ubuntu's extra/), extra-modules leaves the delegate to
        mobile-data: it neither starts sipa-dele-start nor inserts the module itself."""
        for ko in (self.mods / 'sipa-dele.ko', self.mods / 'extra' / 'sipa-dele.ko'):
            ko.parent.mkdir(exist_ok=True)
            ko.write_text('')
            for shell in self.each_shell():
                r, calls = self.run_script(shell, 'extra-modules')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('modprobe mali_kbase', calls)       # it did run
                self.assertNotIn('sipa-dele-start', calls)
                self.assertNotIn('sipa-dele', calls.replace('sipa-dele-start', ''))
            ko.unlink()

    def test_extra_modules_loads_the_mainline_only_modules_when_the_kernel_has_them(self):
        """mu300_thermal (the stock virtual thermal zone) and ums9620_lpintc (the cluster power-down's wake sources,
        FINDINGS 218), mainline only: each loaded once, by name where depmod indexed it and by path where not
        (Ubuntu's extra/ before depmod ran); the vendor 5.4 kernel has no such modules."""
        for m in ('mu300_thermal', 'ums9620_lpintc'):
            for shell in self.each_shell():
                r, calls = self.run_script(shell, 'extra-modules')                   # 5.4: no module, nothing tried
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn(m, calls)
                for sub in ('', 'extra'):
                    ko = self.mods / sub / f'{m}.ko'
                    ko.parent.mkdir(exist_ok=True)
                    ko.write_text('')
                    r, calls = self.run_script(shell, 'extra-modules')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn(f'modprobe {m}', calls)
                    self.assertNotIn(f'insmod {ko}', calls)
                    self.assertIn(f'loaded {m}', r.stdout)
                    r, calls = self.run_script(shell, 'extra-modules', MODPROBE_RC=1)   # no index yet: by path
                    self.assertIn(f'insmod {ko}', calls)
                    self.assertIn(f'loaded {m}', r.stdout)
                    r, calls = self.run_script(shell, 'extra-modules', MODPROBE_RC=1, INSMOD_RC=1)
                    self.assertEqual(r.returncode, 0, r.stderr)                      # a failure is logged, not fatal
                    self.assertIn(f'FAILED {m}', r.stdout)
                    r, calls = self.run_script(shell, 'extra-modules', f'{m} 12288 0 - Live 0x0 (O)\n')
                    self.assertNotIn(m, calls)                                       # loaded already
                    ko.unlink()

    def test_refuses_after_the_wait(self):
        """K46: the packet domain never comes up: exit 1 after MU300_DELE_WAIT s, the module is not inserted."""
        (self.mods / 'sipa-dele.ko').write_text('')
        for shell in self.each_shell():
            t = time.monotonic()
            r, calls = self.run_script(shell, 'sipa-dele-start', MU300_DELE_WAIT=1, CGATT='+CGATT: 0')
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('mu300-at -t 8 AT+CGATT?', calls)
            self.assertNotIn('insmod', calls)
            self.assertIn('refusing', r.stdout)
            self.assertLess(time.monotonic() - t, 8)   # a deadline in seconds (sleep is a no-op here)

    def test_loads_once_the_packet_domain_is_attached(self):
        """+CGATT: 1: the module is inserted from where the system keeps it (flat on OpenWrt, extra/ on Ubuntu)."""
        for sub in ('', 'extra'):
            ko = self.mods / sub / 'sipa-dele.ko'
            ko.parent.mkdir(exist_ok=True)
            ko.write_text('')
            for shell in self.each_shell():
                r, calls = self.run_script(shell, 'sipa-dele-start', MU300_DELE_WAIT=20)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual([c for c in calls.splitlines() if c.startswith('insmod')], [f'insmod {ko}'])
            ko.unlink()

    def test_failures_and_nothing_to_do(self):
        """insmod fails: exit 1 (the dial must not go on). Already loaded, or no module in this kernel: exit 0,
        nothing inserted."""
        (self.mods / 'sipa-dele.ko').write_text('')
        for shell in self.each_shell():
            r, calls = self.run_script(shell, 'sipa-dele-start', MU300_DELE_WAIT=20, INSMOD_RC=1)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('FAILED', r.stdout)
            r, calls = self.run_script(shell, 'sipa-dele-start', 'sipa_dele 16384 0 - Live 0x0 (O)\n',
                                       MU300_DELE_WAIT=20)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(calls, '')
        (self.mods / 'sipa-dele.ko').unlink()
        for shell in self.each_shell():
            r, calls = self.run_script(shell, 'sipa-dele-start', MU300_DELE_WAIT=20)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn('insmod', calls)


class Bootmark(ShellTest):
    """bootmark (K42): one "t=<uptime> words" line per call on tmpfs, -r empties, never fails."""

    def test_two_calls_two_lines_and_reset_empties(self):
        up = self.tmp / 'uptime'
        up.write_text('12.34 56.78\n')
        os.environ['MU300_UPTIME'] = str(up)
        self.addCleanup(os.environ.pop, 'MU300_UPTIME', None)
        for shell in self.each_shell():
            f = self.tmp / 'run' / 'boot-timeline'   # its directory does not exist yet: bootmark creates it
            shutil.rmtree(f.parent, ignore_errors=True)
            for words in ('first mark', 'second'):
                r = self.script(shell, BIN / 'bootmark', *words.split(), MU300_TIMELINE=str(f))
                self.assertEqual(r.returncode, 0, shell)
            lines = f.read_text().splitlines()
            self.assertEqual(len(lines), 2, shell)
            self.assertRegex(lines[0], r'^t=12\.34 +first mark$', shell)
            self.assertRegex(lines[1], r'^t=12\.34 +second$', shell)
            self.script(shell, BIN / 'bootmark', '-r', 'fresh', MU300_TIMELINE=str(f))
            self.assertEqual(len(f.read_text().splitlines()), 1, shell)
            self.assertIn(' fresh', f.read_text(), shell)
            self.script(shell, BIN / 'bootmark', '-r', MU300_TIMELINE=str(f))
            self.assertEqual(f.read_text().strip(), '', shell)

    def test_unwritable_path_is_exit_zero(self):
        for shell in self.each_shell():
            for path in ('/proc/nonexistent/x/boot-timeline', str(self.tmp)):   # no such directory; a directory
                r = self.script(shell, BIN / 'bootmark', 'x', MU300_TIMELINE=path)
                self.assertEqual(r.returncode, 0, (shell, path))
                r = self.script(shell, BIN / 'bootmark', '-r', 'x', MU300_TIMELINE=path)
                self.assertEqual(r.returncode, 0, (shell, path))

    def test_the_default_is_on_tmpfs_and_the_callers_guard_it(self):
        self.assertIn('${MU300_TIMELINE:-/run/mu300/boot-timeline}', (BIN / 'bootmark').read_text())
        self.assertTrue(os.access(BIN / 'bootmark', os.X_OK))
        for f in (BIN / 'android-vendor-start', BIN / 'mobile-data',
                  TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-atd'):
            text = f.read_text()
            self.assertRegex(text, r'\[ -x /opt/mu300/bin/bootmark \] && /opt/mu300/bin/bootmark .*\|\| true', f.name)
            # no bare call: every bootmark call sits behind the guard or inside mark()
            self.assertNotRegex(text, r'(?m)^\s*/opt/mu300/bin/bootmark', f.name)
        self.assertIn('bootmark S19-atd-init', (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-atd').read_text())


class VendorStart(ShellTest):
    """android-vendor-start (K40, K41): the partition links without forks, and no sleep after logdw."""

    def test_link_partitions_from_a_fake_sysfs(self):
        text = (BIN / 'android-vendor-start').read_text()
        body = text[text.index('\nlink_partitions() {'):text.index('\n}\n', text.index('\nlink_partitions() {')) + 3]
        sysdir, dev = self.tmp / 'sys', self.tmp / 'dev'
        (dev / 'block' / 'by-name').mkdir(parents=True)
        for name, uevent in (('mmcblk0p1', 'MAJOR=179\nMINOR=1\nPARTNAME=boot_a\nDEVTYPE=partition\n'),
                             ('mmcblk0p2', 'MAJOR=179\nMINOR=2\nPARTNAME=system a=b\n'),
                             ('mmcblk0p3', 'MAJOR=179\nMINOR=3\n')):    # no PARTNAME: only the block link
            (sysdir / name).mkdir(parents=True)
            (sysdir / name / 'uevent').write_text(uevent)
        (sysdir / 'mmcblk0boot0').mkdir()   # not a partition: not matched by mmcblk0p*
        for shell in self.each_shell():
            r = self.sh(shell, body + f'\nlink_partitions {sysdir} {dev}')
            self.assertEqual(r.returncode, 0, (shell, r.stderr))
            links = {p.relative_to(dev).as_posix(): os.readlink(p) for p in dev.rglob('*') if p.is_symlink()}
            self.assertEqual(links, {
                'block/mmcblk0p1': f'{dev}/mmcblk0p1', 'block/mmcblk0p2': f'{dev}/mmcblk0p2',
                'block/mmcblk0p3': f'{dev}/mmcblk0p3',
                'block/by-name/boot_a': f'{dev}/mmcblk0p1', 'block/by-name/system a=b': f'{dev}/mmcblk0p2'}, shell)
            # a second run over existing links is fine, and an absent sysfs is not an error
            self.assertEqual(self.sh(shell, body + f'\nlink_partitions {sysdir} {dev}').returncode, 0, shell)
            self.assertEqual(self.sh(shell, body + f'\nlink_partitions {self.tmp}/none {dev}').returncode, 0, shell)
            shutil.rmtree(dev / 'block'); (dev / 'block' / 'by-name').mkdir(parents=True)

    def test_slot_without_the_initramfs_file(self):
        # a boot image from before the slot work publishes no /run/mu300/linux-slot: under set -e the script must
        # go on with the slot from the command line, not end before modem_control (seen on F50 #1: no modem)
        text = (BIN / 'android-vendor-start').read_text()
        m = re.search(r'# --- slot begin\n(.*?)# --- slot end', text, re.S)
        self.assertIsNotNone(m, 'android-vendor-start has no slot block')
        cmd = self.tmp / 'cmdline'
        run = self.tmp / 'run'
        for shell in self.each_shell():
            for slot, want in (('a', 'L=a AS=b'), ('b', 'L=b AS=a')):
                cmd.write_text(f'console=ttyS1 androidboot.slot_suffix=_{slot} quiet')
                r = self.sh(shell, f'set -e\nsrc={cmd}\n' + m.group(1) + 'echo "L=$L AS=$AS"', MU300_RUN=run)
                self.assertEqual(r.stdout.strip(), want, (shell, r.stderr))
            (run / 'mu300').mkdir(parents=True, exist_ok=True)
            (run / 'mu300' / 'linux-slot').write_text('a\n')
            cmd.write_text('androidboot.slot_suffix=_b')
            r = self.sh(shell, f'set -e\nsrc={cmd}\n' + m.group(1) + 'echo "L=$L AS=$AS"', MU300_RUN=run)
            self.assertEqual(r.stdout.strip(), 'L=a AS=b', shell)
            shutil.rmtree(run)

    # ---- the imported Android files (issue #127): whatever unpacked them, the daemons can read them afterwards
    def android_tree(self):
        a = self.tmp / 'android'
        shutil.rmtree(a, ignore_errors=True)
        files = {'dev-properties/property_info': 0o444, 'dev-properties/u:object_r:radio_prop:s0': 0o444,
                 'vendor/bin/modem_control': 0o755, 'vendor/etc/modem_cp_info.xml': 0o644,
                 'vendor/lib64/hw/vulkan.so': 0o644, 'system/bin/cltest': 0o755, 'system/lib64/libc++.so': 0o644,
                 'apex/com.android.runtime/bin/linker64': 0o755,
                 'apex/com.android.runtime/lib64/bionic/libc.so': 0o644, 'linkerconfig/ld.config.txt': 0o644,
                 'data/vendor/local/keep': 0o600}       # not an imported tree: never touched
        for rel, mode in files.items():
            f = a / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(rel)
            f.chmod(mode)
        os.symlink('/apex/com.android.runtime/bin/linker64', a / 'system/bin/linker64')
        for d in a.rglob('*'):
            if d.is_dir() and not d.is_symlink():
                d.chmod(0o755)                          # whatever umask the tests run under
        (a / 'dev-properties').chmod(0o711)             # as Android has it
        return a, files

    # the script's own A and mark; the marks go to stdout here
    MODES_PRE = 'set -e\nA=/nonexistent\nmark() { echo "mark $*"; }\n'

    def modes(self, a):
        return {p.relative_to(a).as_posix(): stat.S_IMODE(p.lstat().st_mode)
                for p in a.rglob('*') if not p.is_symlink()}

    def run_modes(self, shell, a):
        text = (BIN / 'android-vendor-start').read_text()
        m = re.search(r'# --- modes begin\n(.*?)# --- modes end', text, re.S)
        self.assertIsNotNone(m, 'android-vendor-start has no modes block')
        (self.tmp / 'kmsg').write_text('')
        return self.sh(shell, self.MODES_PRE + m.group(1) + 'echo done', MU300_ANDROID=a,
                       MU300_KMSG=self.tmp / 'kmsg', MU300_ANDROID_OWNER=f'{os.getuid()}:{os.stat(self.tmp).st_gid}')

    def test_imported_files_installed_0700_become_readable(self):
        # what install.sh stored from a FAT/exFAT work directory on macOS: every file and directory 0700
        for shell in self.each_shell():
            a, files = self.android_tree()
            good = self.modes(a)
            for p in sorted(a.rglob('*'), reverse=True):
                if not p.is_symlink() and p.relative_to(a).parts[0] != 'data':
                    p.chmod(0o700)
            r = self.run_modes(shell, a)
            self.assertEqual(r.stdout.split('\n')[:2], ['mark vendor-modes-repaired', 'done'], (shell, r.stderr))
            want = dict(good, **{'dev-properties': 0o755})      # a 0700 directory becomes 0755, like the others
            self.assertEqual(self.modes(a), want, shell)
            self.assertEqual(os.readlink(a / 'system/bin/linker64'), '/apex/com.android.runtime/bin/linker64')
            # one line that says what was found, with the count: 10 files and 16 directories
            n = len([k for k in good if k.split('/')[0] != 'data'])
            self.assertRegex(r.stderr, rf'android-vendor-start: {n} imported Android files and directories had ', shell)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
            # the same line for the kernel log: OpenWrt's log daemon is not up yet when this runs
            self.assertEqual((self.tmp / 'kmsg').read_text(), r.stderr, shell)
            # and the next start finds nothing to do and says nothing
            r = self.run_modes(shell, a)
            self.assertEqual((r.stdout.strip(), r.stderr), ('done', ''), shell)
            self.assertEqual(self.modes(a), want, shell)

    def test_imported_files_that_are_right_are_left_alone(self):
        for shell in self.each_shell():
            a, files = self.android_tree()
            good = self.modes(a)
            self.assertEqual(good['dev-properties'], 0o711)
            r = self.run_modes(shell, a)
            self.assertEqual((r.stdout.strip(), r.stderr), ('done', ''), shell)
            self.assertEqual(self.modes(a), good, shell)

    def test_imported_files_one_at_a_time(self):
        # vendor/etc alone 0700 (tar made it under umask 077): modem_control ends at "can't get modem img!";
        # a file others can write; a property file that is not read-only; a program that cannot be run
        cases = (('vendor/etc', 0o700, 0o755), ('system/lib64/libc++.so', 0o666, 0o644),
                 ('dev-properties/property_info', 0o600, 0o444), ('vendor/bin/modem_control', 0o644, 0o755),
                 ('apex/com.android.runtime/lib64/bionic/libc.so', 0o4755, 0o644), ('linkerconfig', 0o750, 0o755))
        for shell in self.each_shell():
            for rel, bad, fixed in cases:
                a, files = self.android_tree()
                good = self.modes(a)
                (a / rel).chmod(bad)
                r = self.run_modes(shell, a)
                self.assertEqual(r.stdout.split(), ['mark', 'vendor-modes-repaired', 'done'], (shell, rel, r.stderr))
                self.assertEqual(self.modes(a), dict(good, **{rel: fixed}), (shell, rel))
                self.assertIn(f'1 imported Android files and directories had ', r.stderr, (shell, rel))
                self.assertIn(f'(the first: {a}/{rel})', r.stderr, (shell, rel))

    def test_imported_files_without_the_trees_or_the_right_to_change_them(self):
        for shell in self.each_shell():
            # a system without the imported files (no modem stack): nothing to do, and no error under set -e
            empty = self.tmp / 'none'
            empty.mkdir(exist_ok=True)
            r = self.run_modes(shell, empty)
            self.assertEqual((r.stdout.strip(), r.stderr), ('done', ''), shell)
            # an owner that cannot be set (here: not root) is reported, and the script goes on to start the modem
            a, files = self.android_tree()
            text = (BIN / 'android-vendor-start').read_text()
            m = re.search(r'# --- modes begin\n(.*?)# --- modes end', text, re.S)
            if os.getuid() != 0:
                r = self.sh(shell, self.MODES_PRE + m.group(1) + 'echo done', MU300_ANDROID=a,
                            MU300_KMSG=self.tmp / 'no' / 'kmsg')      # nor a kernel log to write to
                self.assertEqual(r.stdout.split(), ['mark', 'vendor-modes-wrong', 'done'], (shell, r.stderr))
                self.assertIn('could not be changed', r.stderr, shell)
                self.assertIn('the modem may not start', r.stderr, shell)

    def test_the_modes_block_runs_before_the_daemons_and_owns_to_root(self):
        text = (BIN / 'android-vendor-start').read_text()
        self.assertLess(text.index('# --- modes end'), text.index('exec /opt/mu300/bin/android-run modem_control'))
        self.assertIn('MO=${MU300_ANDROID_OWNER:-0:0}', text)
        self.assertIn('MA=${MU300_ANDROID:-$A}', text)
        m = re.search(r'# --- modes begin\n(.*?)# --- modes end', text, re.S).group(1)
        # nothing mounted below is entered, links are never followed or changed
        self.assertEqual(len(re.findall(r'\bfind \$MT -xdev ', m)), 6)
        self.assertEqual(m.count('find '), 6)
        self.assertNotIn('chmod -R', m)
        self.assertNotIn('chown -R', m)

    def test_the_vendor_log_has_a_directory_to_go_to(self):
        # OpenWrt runs this at S09, its boot script makes /var/log at S10: without the directory the redirection
        # failed, logdw never started and the daemons' reasons for a modem that did not boot were lost (seen on
        # the F50-B: no /var/log/android-logcat.txt, no logdw process, until the service was restarted by hand)
        text = (BIN / 'android-vendor-start').read_text()
        self.assertLess(text.index('mkdir -p /var/log 2>/dev/null || true\n'),
                        text.index('/opt/mu300/bin/logdw /dev/socket/logdw >> /var/log/android-logcat.txt'))
        self.assertEqual((TOP / 'openwrt/overlay/etc/init.d/mu300-vendor').read_text().count('\nSTART=09\n'), 1)

    def test_no_forks_in_the_loop_and_no_sleep_after_logdw(self):
        text = (BIN / 'android-vendor-start').read_text()
        body = text[text.index('\nlink_partitions() {'):text.index('\n}\n', text.index('\nlink_partitions() {'))]
        for cmd in ('basename', 'dirname', 'sed', '$(', '`'):
            self.assertNotIn(cmd, body)
        self.assertNotRegex(text, r'logdw[^\n]*sleep 1')
        self.assertNotRegex(text, r'(?m)^\s*\[ -S /dev/socket/logdw \] \|\|')


class Os(ShellTest):
    """mu300-os against a fake disk area: three systems, and a kept copy is never offered."""
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        for name in ('ubuntu', 'openwrt', 'openwrt-luci', 'openwrt-luci.old'):
            init = self.disk / name / 'sbin' / 'init'
            init.parent.mkdir(parents=True)
            init.write_text('#!/bin/sh\n'); init.chmod(0o755)
            (self.disk / name / 'etc').mkdir()
        self.root = self.tmp / 'root'
        (self.root / 'etc' / 'mu300').mkdir(parents=True)
        (self.root / 'etc' / 'mu300' / 'default-boot').write_text('linux\n')
        self.stub('mountpoint', 'exit 0')

    def os(self, shell, *args):
        return self.script(shell, BIN / 'mu300-os', *args, MU300_DISK=self.disk, MU300_SYSROOT=self.root)

    def test_lists_the_third_system(self):
        for shell in self.each_shell():
            out = self.os(shell).stdout
            self.assertIn('openwrt-luci: installed', out)
            self.assertNotIn('.old', out)

    def test_choosing_it(self):
        for shell in self.each_shell():
            (self.disk / 'openwrt-luci' / 'etc' / 'mu300' / 'default-boot').unlink(missing_ok=True)
            r = self.os(shell, 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.disk / '.mu300' / 'boot-os').read_text().strip(), 'openwrt-luci')
            self.assertEqual((self.disk / 'openwrt-luci' / 'etc' / 'mu300' / 'default-boot').read_text().strip(), 'linux')

    def test_a_kept_copy_cannot_be_chosen(self):
        for shell in self.each_shell():
            r = self.os(shell, 'openwrt-luci.old')
            self.assertEqual(r.returncode, 1)
            self.assertFalse((self.disk / '.mu300' / 'boot-os').exists())


class Led(ShellTest):
    # the U30 Air's: the battery LED (the PMIC's RGB, its green channel white) shows the battery as ZTE's firmware
    # does (FINDINGS 151: white at 120 of 255 from 60 %, blue 200 from 10 %, red 150 below), the network LED by the SIM
    # in use (FINDINGS 154): white for slot 0 (zte-ldo0), blue for slot 1 (net_blue), red without service
    # (keyboard-backlight); the Wi-Fi LED's colours are LDOs too
    LEDS = ['sc27xx:blue', 'sc27xx:red', 'sc27xx:green', 'net_blue', 'keyboard-backlight', 'zte-ldo0', 'zte-ldo1', 'zte-ldo2']

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.setUp_leds()
        (self.root / 'run' / 'mu300').mkdir(parents=True)
        (self.root / 'proc').mkdir()
        self.conf = self.tmp / 'led.conf'
        self.uptime(100)
        self.stub('iw', 'cat "$STUBLOG/iw.out" 2>/dev/null')
        self.battery()

    def battery(self, capacity=80, status='Discharging', online=0):
        """the fuel gauge and the charger as mainline has them (sc27xx-fgu, bq256xx-charger)"""
        ps = self.root / 'sys/class/power_supply'
        (ps / 'sc27xx-fgu').mkdir(parents=True, exist_ok=True)
        (ps / 'bq256xx-charger').mkdir(parents=True, exist_ok=True)
        (ps / 'sc27xx-fgu/capacity').write_text(f'{capacity}\n')
        (ps / 'sc27xx-fgu/status').write_text(f'{status}\n')
        (ps / 'bq256xx-charger/online').write_text(f'{online}\n')

    def setUp_leds(self):
        for n in self.LEDS:
            d = self.root / 'sys' / 'class' / 'leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n')
            (d / 'max_brightness').write_text('255\n')
            (d / 'trigger').write_text('[none] timer\n')

    def uptime(self, s):
        (self.root / 'proc' / 'uptime').write_text(f'{s}.42 1234.00\n')

    def reset(self):
        for n in self.LEDS:
            (self.root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
        for f in (self.root / 'run/mu300').glob('led/*'):
            f.unlink()
        for f in (self.root / 'run/mu300').glob('led/.*'):
            f.unlink()
        self.conf.unlink(missing_ok=True)
        self.battery()

    def state(self):
        return {n: (self.root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in self.LEDS}

    def led(self, shell, device, *args):
        # replaced, not rewritten in place: a siren that the call before started ("alarm on") is a new mu300-led that
        # asks mu300-device for the device, and write_text truncates first - a siren that read the file empty in
        # between took the device for an F50 and lit the PMIC's LED on a V50 (CI, 2026-10-18). On a device the file
        # is written once, at boot.
        dev = self.root / 'run/mu300/device'
        new = dev.with_name('device.new')
        new.write_text(device + '\n')
        os.replace(new, dev)
        return self.script(shell, BIN / 'mu300-led', *args, MU300_SYSROOT=self.root, MU300_BIN=BIN,
                           MU300_LED_CONF=self.conf)

    def sim(self, slot, openwrt=True):
        """this boot's SIM slot as mu300-atd pins it; slot 1 exists only on OpenWrt (mu300-sim)"""
        p = self.root / 'run/mu300/sim-slot'
        if slot is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(f'{slot}\n')
        rel = self.root / 'etc/openwrt_release'
        if openwrt:
            rel.parent.mkdir(parents=True, exist_ok=True)
            rel.write_text('DISTRIB_ID=OpenWrt\n')
        else:
            rel.unlink(missing_ok=True)

    def test_u30air(self):
        # as ZTE's firmware (FINDINGS 154): the network LED shows WHICH SIM is in use, whatever 4G or 5G
        for shell in self.each_shell():
            self.reset()
            self.sim(0)
            self.assertEqual(self.led(shell, 'u30air', 'power', 'on').returncode, 0)
            self.assertEqual(self.led(shell, 'u30air', 'data', 'error').returncode, 0)
            s = self.state()
            self.assertEqual((s['sc27xx:green'], s['keyboard-backlight'], s['net_blue'], s['zte-ldo0']),
                             ('120', '255', '0', '0'))
            for slot, lit, dark in ((0, 'zte-ldo0', 'net_blue'), (1, 'net_blue', 'zte-ldo0')):
                self.sim(slot)
                for tech in ('on', '5g', 'on'):
                    self.led(shell, 'u30air', 'data', tech)
                    s = self.state()
                    self.assertEqual((s['keyboard-backlight'], s[lit], s[dark]), ('0', '255', '0'), (slot, tech))
            self.led(shell, 'u30air', 'data', 'error')
            s = self.state()
            self.assertEqual((s['keyboard-backlight'], s['net_blue'], s['zte-ldo0']), ('255', '0', '0'))
            self.led(shell, 'u30air', 'data', 'off')
            s = self.state()
            self.assertEqual((s['keyboard-backlight'], s['net_blue'], s['zte-ldo0']), ('0', '0', '0'))
            self.assertEqual(self.state()['sc27xx:blue'], '0')  # the F50's LED is not touched

    def test_u30air_sim_slot_sources(self):
        """The slot is /run/mu300/sim-slot, never created here; slot 1 only where it is supported (OpenWrt)."""
        for shell in self.each_shell():
            for slot, openwrt, lit in ((None, True, 'zte-ldo0'),     # not pinned yet: slot 0, and no pin made
                                       ('1', False, 'zte-ldo0'),    # Ubuntu: slot 1 is ignored, every reader uses 0
                                       ('junk', True, 'zte-ldo0'),
                                       ('1', True, 'net_blue')):
                self.reset()
                self.sim(slot, openwrt)
                self.led(shell, 'u30air', 'power', 'on')
                self.led(shell, 'u30air', 'data', 'on')
                s = self.state()
                self.assertEqual((s[lit], s['net_blue' if lit == 'zte-ldo0' else 'zte-ldo0']), ('255', '0'),
                                 (slot, openwrt))
                if slot is None:
                    self.assertFalse((self.root / 'run/mu300/sim-slot').exists())

    def test_u30air_live_sim_switch_blinks_the_target(self):
        """mu300-sim-u30's live switch: the target card's colour blinks (the timer trigger) through the hang-up the
        switch causes; the dial on the new card shows it steady; once the switch is over, off/error count again."""
        for shell in self.each_shell():
            self.reset()
            self.sim(0)
            self.conf.write_text('LED_TIMEOUT=0\n')
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'data', 'on')
            self.assertEqual(self.state()['zte-ldo0'], '255')
            marker = self.root / 'run/mu300/sim-switching'
            marker.touch()
            self.assertEqual(self.led(shell, 'u30air', 'data', 'switching', '1').returncode, 0)
            trig = lambda n: (self.root / 'sys/class/leds' / n / 'trigger').read_text().strip()
            self.assertEqual(trig('net_blue'), 'timer')
            self.assertEqual((self.root / 'sys/class/leds/net_blue/delay_on').read_text().strip(), '500')
            s = self.state()
            self.assertEqual((s['zte-ldo0'], s['keyboard-backlight']), ('0', '0'))
            # netifd's teardown of the old connection, whenever it comes: the blink stays
            for st in ('off', 'error'):
                self.led(shell, 'u30air', 'data', st)
                self.assertEqual((trig('net_blue'), self.state()['keyboard-backlight']), ('timer', '0'), st)
            # a sleep and a wake keep the blink
            self.conf.write_text('LED_TIMEOUT=30\n')
            self.led(shell, 'u30air', 'sleep')
            self.assertEqual(trig('net_blue'), 'none')
            self.led(shell, 'u30air', 'wake')
            self.assertEqual(trig('net_blue'), 'timer')
            self.conf.write_text('LED_TIMEOUT=0\n')
            # published, dialled: steady blue
            self.sim(1)
            marker.unlink()
            self.led(shell, 'u30air', 'data', 'on')
            s = self.state()
            self.assertEqual((trig('net_blue'), s['net_blue'], s['zte-ldo0']), ('none', '255', '0'))
            # a switch that ended without a dial (marker gone): off and error are shown as usual
            marker.touch()
            self.led(shell, 'u30air', 'data', 'switching', '0')
            self.assertEqual(trig('zte-ldo0'), 'timer')
            marker.unlink()
            self.led(shell, 'u30air', 'data', 'error')
            s = self.state()
            self.assertEqual((trig('zte-ldo0'), s['zte-ldo0'], s['keyboard-backlight']), ('none', '0', '255'))
            # without the marker a stray announcement does not hold off/error back either
            self.led(shell, 'u30air', 'data', 'switching', '1')
            self.led(shell, 'u30air', 'data', 'off')
            self.assertEqual((trig('net_blue'), self.state()['net_blue']), ('none', '0'))
            # the slot is checked; other devices ignore the command
            self.assertEqual(self.led(shell, 'u30air', 'data', 'switching', '2').returncode, 2)
            self.assertEqual(self.led(shell, 'u30air', 'data', 'switching', '1;x').returncode, 2)
            self.assertEqual(self.led(shell, 'u30air', 'data', 'switching').returncode, 2)
            self.reset()
            self.led(shell, 'f50', 'data', 'on')
            self.assertEqual(self.led(shell, 'f50', 'data', 'switching', '1').returncode, 0)
            self.assertEqual(self.state()['sc27xx:blue'], '255')

    def test_u30air_switch_blink_on_5_4_is_steady(self):
        # ZTE's zte_ldo_leds under 5.4 has no trigger: the target colour is simply on
        for shell in self.each_shell():
            self.reset()
            self.sim(0)
            shutil.rmtree(self.root / 'sys/class/leds/zte-ldo0')
            vendor = self.root / 'sys/devices/platform/zte_ldo_leds'
            vendor.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                (vendor / f'vddcama{i}_status').write_text('0\n')
            self.conf.write_text('LED_TIMEOUT=0\n')
            (self.root / 'run/mu300/sim-switching').touch()
            self.led(shell, 'u30air', 'data', 'switching', '0')
            self.assertEqual((vendor / 'vddcama0_status').read_text().strip(), '1')
            (self.root / 'run/mu300/sim-switching').unlink()
            shutil.rmtree(self.root / 'sys/devices')
            self.setUp_leds()

    def test_wifi_colour_follows_the_band(self):
        for shell in self.each_shell():
            self.reset()
            self.led(shell, 'u30air', 'power', 'on')
            # as ZTE's firmware (FINDINGS 152): white (VDDCAMA1) on 5 GHz, blue (VDDCAMA2) on 2.4 GHz
            for iw, lit, dark in (('channel 36 (5180 MHz), width: 80 MHz', 'zte-ldo1', 'zte-ldo2'),
                                  ('channel 6 (2437 MHz), width: 20 MHz', 'zte-ldo2', 'zte-ldo1')):
                (self.tmp / 'iw.out').write_text(f'Interface wlan0\n\ttype AP\n\t{iw}\n')
                self.led(shell, 'u30air', 'wifi', 'on')
                s = self.state()
                self.assertEqual((s[lit], s[dark]), ('255', '0'), iw)
            self.led(shell, 'u30air', 'wifi', 'off')
            self.assertEqual((self.state()['zte-ldo2'], self.state()['zte-ldo1']), ('0', '0'))
            # the hotspot just started and has no channel yet: its setting decides
            (self.tmp / 'iw.out').write_text('Interface wlan0\n\ttype AP\n')
            conf = self.tmp / 'hotspot.conf'
            for band, lit in (('2.4', 'zte-ldo2'), ('5', 'zte-ldo1')):
                conf.write_text(f'SSID=x\nPSK=12345678\nBAND={band}\n')
                (self.root / 'run/mu300/device').write_text('u30air\n')
                self.script(shell, BIN / 'mu300-led', 'wifi', 'on', MU300_SYSROOT=self.root, MU300_BIN=BIN,
                            MU300_LED_CONF=self.conf, MU300_HOTSPOT_CONF=conf)
                self.assertEqual(self.state()[lit], '255', band)

    def test_timeout(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.led(shell, 'u30air', 'power', 'on')          # boot: lit for 60 s
            self.led(shell, 'u30air', 'data', 'on')
            self.uptime(150)
            self.led(shell, 'u30air', 'sleep', '--if-due')    # not yet
            self.assertEqual(self.state()['zte-ldo0'], '255')   # slot 0: white
            self.uptime(161)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual({v for v in self.state().values()}, {'0'})
            # a change while dark is kept, and shown on the next wake
            self.led(shell, 'u30air', 'data', 'error')
            self.assertEqual(self.state()['keyboard-backlight'], '0')
            self.led(shell, 'u30air', 'wake')
            s = self.state()
            self.assertEqual((s['sc27xx:green'], s['keyboard-backlight'], s['zte-ldo0']), ('120', '255', '0'))
            # and the timeout counts from the wake
            self.uptime(161 + 59)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['sc27xx:green'], '120')
            self.uptime(161 + 61)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['sc27xx:green'], '0')

    def test_idle_darkens_everything_but_a_key_still_shows_them(self):
        """2026-10-07: under the idle flag a key press lit nothing, and the device looked powered off for hours. A
        key's wake now shows the LEDs for LED_TIMEOUT (a minute when it is 0) in idle too; then dark again."""
        for shell in self.each_shell():
            for timeout, lit_for in (('', 60), ('LED_TIMEOUT=0\n', 60), ('LED_TIMEOUT=20\n', 20)):
                self.reset()
                self.uptime(100)
                if timeout:
                    self.conf.write_text(timeout)
                self.sim(1)                                                # the eSIM: blue
                self.led(shell, 'u30air', 'power', 'on')
                self.led(shell, 'u30air', 'data', 'on')
                self.assertEqual(self.state()['net_blue'], '255')
                self.assertEqual(self.led(shell, 'u30air', 'idle', 'on').returncode, 0)
                self.assertEqual((self.state()['sc27xx:green'], self.state()['net_blue']), ('0', '0'))
                self.sim(0)                                                # (a live switch)
                self.led(shell, 'u30air', 'data', '5g')                    # remembered, not shown
                self.led(shell, 'u30air', 'power', 'on')
                self.assertEqual(set(self.state().values()), {'0'}, timeout)
                self.led(shell, 'u30air', 'sleep', '--if-due')             # nothing to put out
                self.assertEqual(set(self.state().values()), {'0'}, timeout)
                # a key: lit, idle or not
                self.uptime(200)
                self.led(shell, 'u30air', 'wake')
                s = self.state()
                self.assertEqual((s['sc27xx:green'], s['zte-ldo0'], s['net_blue']), ('120', '255', '0'), timeout)
                self.sim(1)
                self.led(shell, 'u30air', 'data', 'on')                    # a change meanwhile shows too
                self.assertEqual(self.state()['net_blue'], '255', timeout)
                self.uptime(200 + lit_for - 1)
                self.led(shell, 'u30air', 'sleep', '--if-due')
                self.assertEqual(self.state()['sc27xx:green'], '120', timeout)
                self.uptime(200 + lit_for + 1)
                self.led(shell, 'u30air', 'sleep', '--if-due')
                self.assertEqual(set(self.state().values()), {'0'}, timeout)   # still idle: dark again
                self.led(shell, 'u30air', 'data', 'on')
                self.assertEqual(self.state()['net_blue'], '0', timeout)
                # idle off: as LED_TIMEOUT says (0: always on; else from the last wake)
                self.led(shell, 'u30air', 'wake')
                self.led(shell, 'u30air', 'idle', 'off')
                s = self.state()
                self.assertEqual((s['sc27xx:green'], s['net_blue']), ('120', '255'), timeout)

    def test_idle_off_after_the_timeout_stays_dark(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'idle', 'on')
            self.uptime(100 + 120)
            self.led(shell, 'u30air', 'idle', 'off')
            self.assertEqual(set(self.state().values()), {'0'})
            self.led(shell, 'u30air', 'wake')
            self.assertEqual(self.state()['sc27xx:green'], '120')

    # --- the U30 Air's battery LED as ZTE's firmware (FINDINGS 151)
    def rgb(self):
        s = self.state()
        return s['sc27xx:red'], s['sc27xx:green'], s['sc27xx:blue']

    def test_battery_colour_by_level(self):
        for shell in self.each_shell():
            for cap, want in ((100, ('0', '120', '0')), (60, ('0', '120', '0')), (59, ('0', '0', '200')),
                              (10, ('0', '0', '200')), (9, ('150', '0', '0'))):
                self.reset()
                self.conf.write_text('LED_TIMEOUT=0\n')
                self.battery(cap)
                self.led(shell, 'u30air', 'power', 'on')
                self.assertEqual(self.rgb(), want, cap)
                self.assertEqual((self.root / 'sys/class/leds/sc27xx:green/trigger').read_text().strip(), 'none')
                # the F50: the same PMIC LED is its network light, never the battery's
                self.reset()
                self.battery(cap)
                self.led(shell, 'f50', 'battery')
                self.assertEqual(self.rgb(), ('0', '0', '0'), cap)

    def test_charging_breathes_on_the_pmic_curve_and_is_not_restarted(self):
        for shell in self.each_shell():
            self.reset()
            self.conf.write_text('LED_TIMEOUT=0\n')
            blue = self.root / 'sys/class/leds/sc27xx:blue'
            (blue / 'trigger').write_text('[none] timer pattern\n')
            self.battery(40, 'Charging', 1)
            self.led(shell, 'u30air', 'battery')
            self.assertEqual((blue / 'trigger').read_text().strip(), 'pattern')
            self.assertEqual((blue / 'repeat').read_text().strip(), '-1')
            self.assertEqual((blue / 'hw_pattern').read_text().strip(), '200 320 200 480 200 480 200 720')
            # the next tick finds nothing changed: the curve is not written again (it would restart)
            (blue / 'hw_pattern').write_text('untouched\n')
            self.led(shell, 'u30air', 'tick')
            self.assertEqual((blue / 'hw_pattern').read_text().strip(), 'untouched')
            # full on the charger: steady white
            self.battery(100, 'Full', 1)
            self.led(shell, 'u30air', 'battery')
            self.assertEqual(self.rgb()[1:], ('120', '0'))
            self.assertEqual((self.root / 'sys/class/leds/sc27xx:green/trigger').read_text().strip(), 'none')
            # the gauge still says Charging after an unplug: the charger's online flag decides
            self.battery(40, 'Charging', 0)
            self.led(shell, 'u30air', 'battery')
            self.assertEqual(self.rgb(), ('0', '0', '200'))
            self.assertEqual((blue / 'trigger').read_text().strip(), 'none')
            (blue / 'trigger').write_text('[none] timer\n')

    def test_breathing_after_a_steady_light_stops_the_steady_output_first(self):
        # the SC27xx's pattern_set leaves the steady TYPE bit set: kanoqwq read 0x300 (RUN and steady) on a U30 Air
        # whose LED went from steady to the pattern trigger; brightness 0 first clears it (edcdeb27). A channel
        # already on the pattern trigger is not stopped again.
        for shell in self.each_shell():
            self.reset()
            self.conf.write_text('LED_TIMEOUT=0\n')
            blue = self.root / 'sys/class/leds/sc27xx:blue'
            (blue / 'trigger').write_text('[none] timer pattern\n')
            self.battery(40, 'Not charging', 1)
            self.led(shell, 'u30air', 'battery')
            self.assertEqual((blue / 'brightness').read_text().strip(), '200')          # steady blue
            (blue / 'trigger').write_text('[none] timer pattern\n')                  # as sysfs lists them
            self.battery(40, 'Charging', 1)
            self.led(shell, 'u30air', 'battery')
            self.assertEqual((blue / 'brightness').read_text().strip(), '0')
            self.assertEqual((blue / 'trigger').read_text().strip(), 'pattern')
            self.assertEqual((blue / 'hw_pattern').read_text().strip(), '200 320 200 480 200 480 200 720')
            # already breathing (the trigger shows [pattern]) when it is shown again: no brightness write
            (blue / 'trigger').write_text('none timer [pattern]\n')
            (blue / 'brightness').write_text('77\n')
            (self.root / 'run/mu300/led/.battery-shown').unlink()
            self.led(shell, 'u30air', 'battery')
            self.assertEqual((blue / 'brightness').read_text().strip(), '77')
            self.assertEqual((blue / 'trigger').read_text().strip(), 'none timer [pattern]')

    def test_outside_power_keeps_the_leds_lit_and_an_unplug_restarts_the_timeout(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'data', 'on')                 # slot 0: white (zte-ldo0, FINDINGS 154)
            self.battery(70, 'Charging', 1)
            self.led(shell, 'u30air', 'tick')
            self.uptime(10000)                                   # long past the timeout: on the charger, lit
            self.led(shell, 'u30air', 'tick')
            self.assertEqual(self.state()['zte-ldo0'], '255')
            self.assertNotEqual(self.rgb(), ('0', '0', '0'))
            self.battery(70, 'Discharging', 0)                    # unplugged: 60 s from now
            self.led(shell, 'u30air', 'tick')
            self.assertEqual((self.root / 'run/mu300/led/.awake-until').read_text().strip(), '10060')
            self.assertEqual(self.rgb(), ('0', '120', '0'))
            self.uptime(10059)
            self.led(shell, 'u30air', 'tick')
            self.assertEqual(self.state()['zte-ldo0'], '255')
            self.uptime(10061)
            self.led(shell, 'u30air', 'tick')
            self.assertEqual(set(self.state().values()), {'0'})
            # plugged in again: everything back at once
            self.battery(70, 'Charging', 1)
            self.led(shell, 'u30air', 'tick')
            self.assertEqual(self.state()['zte-ldo0'], '255')

    def test_low_battery_red_does_not_time_out(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.battery(8)
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'data', 'on')
            self.uptime(200)
            self.led(shell, 'u30air', 'tick')
            s = self.state()
            self.assertEqual((s['net_blue'], s['sc27xx:red']), ('0', '150'))   # the rest dark, the red still lit
            # shutdown: dark, and no tick lights it again
            self.led(shell, 'u30air', 'power', 'off')
            self.assertEqual(self.rgb(), ('0', '0', '0'))
            self.led(shell, 'u30air', 'tick')
            self.assertEqual(self.rgb(), ('0', '0', '0'))

    def test_the_heat_alarm_goes_over_the_battery_led(self):
        for shell in self.each_shell():
            self.reset()
            self.conf.write_text('LED_TIMEOUT=0\n')
            self.battery(40)
            self.led(shell, 'u30air', 'power', 'on')
            pid = self.root / 'run/mu300/led-siren.pid'
            try:
                self.led(shell, 'u30air', 'alarm', 'on')
                time.sleep(0.6)
                self.battery(90)
                self.led(shell, 'u30air', 'battery')               # a tick meanwhile writes nothing
                self.assertTrue(pid.exists())
            finally:
                self.led(shell, 'u30air', 'alarm', 'off')
            time.sleep(0.4)
            self.assertEqual(self.rgb(), ('0', '120', '0'))        # the battery as it is now

    def test_charging_boot_shows_the_charge(self):
        # the charging boot: the U30 Air's battery LED breathes in the battery's colour, as whenever it charges
        for shell in self.each_shell():
            self.reset()
            self.battery(40, 'Charging', 1)
            d = self.root / 'sys/class/leds/sc27xx:blue'
            self.assertEqual(self.led(shell, 'u30air', 'charge', 'on').returncode, 0)
            self.assertEqual((d / 'trigger').read_text().strip(), 'timer')     # no pattern trigger here
            self.assertEqual(((d / 'delay_on').read_text().strip(), (d / 'delay_off').read_text().strip()),
                             ('800', '1200'))
            self.assertEqual(self.led(shell, 'u30air', 'charge', 'off').returncode, 0)
            self.assertEqual(self.state()['sc27xx:blue'], '200')
            # a device without a power LED: nothing to blink, no error
            self.assertEqual(self.led(shell, 'f50', 'charge', 'on').returncode, 0)

    def test_no_timeout(self):
        for shell in self.each_shell():
            for device, conf in (('u30air', 'LED_TIMEOUT=0\n'), ('f50', '')):
                self.reset()
                if conf:
                    self.conf.write_text(conf)
                self.uptime(100)
                self.led(shell, device, 'power', 'on')
                self.led(shell, device, 'data', 'on')
                self.uptime(100000)
                self.led(shell, device, 'sleep', '--if-due')
                lit = 'zte-ldo0' if device == 'u30air' else 'sc27xx:blue'   # (U30 Air: slot 0, white)
                self.assertEqual(self.state()[lit], '255', device)

    def test_v50(self):
        # the ZTE V50 (FINDINGS 95, kano's mapping): the network LED is the lamp of the SIM in use, as ZTE's firmware
        # (FINDINGS 199): slot 0 NET2, slot 1 NET1 (blue 4G, white 5G, red without service), the other lamp dark; its
        # Wi-Fi lamp white on 5 GHz and blue on 2.4 GHz; the PMIC's LEDs, which its device tree has as well, are never
        # written, and the lamps go no brighter than the driver allows
        lamps = {f'v50:{l}:{c}': 80 if c == 'white' else 120 for l in ('net1', 'net2', 'wifi') for c in ('white', 'blue', 'red')}
        for n, m in lamps.items():
            d = self.root / 'sys' / 'class' / 'leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'max_brightness').write_text(f'{m}\n')
            (d / 'trigger').write_text('[none] timer\n')

        def lamp_state():
            return {n: (self.root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in lamps}
        for shell, slot in [(sh, sl) for sh in self.each_shell() for sl in (None, 0, 1)]:
            self.sim(slot)
            lamp, other = ('net1', 'net2') if slot == 1 else ('net2', 'net1')
            net1 = tuple(f'v50:{lamp}:{c}' for c in ('red', 'white', 'blue'))
            self.reset()
            for n in lamps:
                (self.root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
            for args in (('power', 'on'), ('wake',), ('sleep',), ('power', 'off')):
                self.assertEqual(self.led(shell, 'v50', *args).returncode, 0, args)   # no power LED, no timeout
            self.assertEqual(set(lamp_state().values()), {'0'})
            for args, lit in ((('data', 'error'), f'v50:{lamp}:red'), (('data', 'on'), f'v50:{lamp}:blue'),
                              (('data', '5g'), f'v50:{lamp}:white'), (('data', 'on'), f'v50:{lamp}:blue'),
                              (('data', 'error'), f'v50:{lamp}:red')):
                self.assertEqual(self.led(shell, 'v50', *args).returncode, 0, args)
                s = lamp_state()
                self.assertEqual({n: s[n] for n in net1}, {n: str(lamps[n]) if n == lit else '0' for n in net1}, args)
            self.led(shell, 'v50', 'data', 'off')
            self.assertEqual({lamp_state()[n] for n in net1}, {'0'})
            for iw, lit, dark in (('channel 36 (5180 MHz), width: 80 MHz', 'v50:wifi:white', 'v50:wifi:blue'),
                                  ('channel 6 (2437 MHz), width: 20 MHz', 'v50:wifi:blue', 'v50:wifi:white')):
                (self.tmp / 'iw.out').write_text(f'Interface wlan0\n\tssid V50\n\ttype AP\n\t{iw}\n')
                self.led(shell, 'v50', 'wifi', 'on')
                s = lamp_state()
                self.assertEqual((s[lit], s[dark]), (str(lamps[lit]), '0'), iw)
            self.led(shell, 'v50', 'wifi', 'off')
            self.assertEqual((lamp_state()['v50:wifi:white'], lamp_state()['v50:wifi:blue']), ('0', '0'))
            self.assertEqual({lamp_state()[f'v50:{other}:{c}'] for c in ('white', 'blue', 'red')}, {'0'})
            self.assertEqual(set(self.state().values()), {'0'})   # no PMIC LED, nothing of the F50's or U30 Air's
        self.sim(None)
        # a V50 under 5.4 (no driver: no lamps): every LED is skipped, the PMIC's are still not written
        for n in lamps:
            shutil.rmtree(self.root / 'sys' / 'class' / 'leds' / n)
        for shell in self.each_shell():
            self.reset()
            for args in (('data', 'on'), ('data', 'error'), ('wifi', 'on'), ('alarm', 'on')):
                self.assertEqual(self.led(shell, 'v50', *args).returncode, 0, args)
            self.led(shell, 'v50', 'alarm', 'off')
            self.assertEqual(set(self.state().values()), {'0'})

    def test_f50(self):
        # ZTE's F50 (read from its Android): one network light on the PMIC's RGB LED - blue on 4G, white (the green
        # channel) on 5G, red without service - and the Wi-Fi light on the keypad backlight sink, at ZTE's 48
        rgb = ('sc27xx:red', 'sc27xx:green', 'sc27xx:blue')
        for shell in self.each_shell():
            self.reset()
            for args in (('power', 'on'), ('wake',), ('sleep',), ('power', 'off')):
                # no power LED and no timeout on an F50: no error, nothing lit
                self.assertEqual(self.led(shell, 'f50', *args).returncode, 0, args)
            self.assertEqual(set(self.state().values()), {'0'})
            for args, lit in ((('data', 'error'), 'sc27xx:red'), (('data', 'on'), 'sc27xx:blue'),
                              (('data', '5g'), 'sc27xx:green'), (('data', 'on'), 'sc27xx:blue'),
                              (('data', 'error'), 'sc27xx:red'), (('data', '5g'), 'sc27xx:green')):
                self.assertEqual(self.led(shell, 'f50', *args).returncode, 0, args)
                s = self.state()
                self.assertEqual({n: s[n] for n in rgb}, {n: '255' if n == lit else '0' for n in rgb}, args)
            self.led(shell, 'f50', 'data', 'off')
            self.assertEqual({self.state()[n] for n in rgb}, {'0'})
            for iw in ('channel 36 (5180 MHz), width: 80 MHz', 'channel 6 (2437 MHz), width: 20 MHz'):
                (self.tmp / 'iw.out').write_text(f'Interface wlan0\n\tssid F50\n\ttype AP\n\t{iw}\n')
                self.led(shell, 'f50', 'wifi', 'on')       # one colour, whichever band
                self.assertEqual(self.state()['keyboard-backlight'], '48', iw)
                self.led(shell, 'f50', 'wifi', 'off')
                self.assertEqual(self.state()['keyboard-backlight'], '0', iw)
            # none of the U30 Air's own LEDs switched
            self.led(shell, 'f50', 'data', 'on')
            self.led(shell, 'f50', 'wifi', 'on')
            s = self.state()
            self.assertEqual((s['net_blue'], s['zte-ldo0'], s['zte-ldo1'], s['zte-ldo2']), ('0', '0', '0', '0'))

    def test_wifi_sync_follows_a_slow_hotspot(self):
        # LuCI turned Wi-Fi on, hostapd comes up seconds later: "wifi sync S" keeps looking and lights it then
        import threading
        ap = 'Interface wlan0\n\tssid F50\n\ttype AP\n\tchannel 36 (5180 MHz), width: 80 MHz\n'
        for shell in self.each_shell():
            self.reset()
            (self.tmp / 'iw.out').write_text('Interface wlan0\n\ttype AP\n')
            t = threading.Timer(1.0, lambda: (self.tmp / 'iw.out').write_text(ap))
            t.start()
            try:
                self.assertEqual(self.led(shell, 'f50', 'wifi', 'sync', '4').returncode, 0)
            finally:
                t.cancel()
            self.assertEqual(self.state()['keyboard-backlight'], '48')

    def test_wifi_sync(self):
        # OpenWrt has no hook when LuCI turns Wi-Fi off or on: "wifi sync" reads whether wlan0 is a running AP
        for shell in self.each_shell():
            for device, lit, level in (('f50', 'keyboard-backlight', '48'), ('u30air', 'zte-ldo1', '255')):
                self.reset()
                self.conf.write_text('LED_TIMEOUT=0\n')
                (self.tmp / 'iw.out').write_text('Interface wlan0\n\tssid F50\n\ttype AP\n'
                                                  '\tchannel 36 (5180 MHz), width: 80 MHz\n')
                self.assertEqual(self.led(shell, device, 'wifi', 'sync').returncode, 0)
                self.assertEqual(self.state()[lit], level, device)
                for iw in ('Interface wlan0\n\ttype AP\n', 'Interface wlan0\n\ttype managed\n', ''):
                    (self.tmp / 'iw.out').write_text(iw)       # an AP without SSID is a stopped hostapd
                    self.led(shell, device, 'wifi', 'on')
                    self.led(shell, device, 'wifi', 'sync')
                    self.assertEqual(self.state()[lit], '0', (device, iw))

    def test_events_follow_hostapd_on_ubus(self):
        # OpenWrt: "events" lights the Wi-Fi LED from hostapd's ubus object as it comes and goes, whoever stopped or
        # started the access point. The fake ubusd: "listen" plays STUBLOG/events, one line each 0.3 s, and before
        # each line sets what "call hostapd.wlan0 get_status" answers (STUBLOG/status, empty: no such object).
        self.stub('ubus', r'''case $1 in
listen) while IFS='|' read -r st ev; do printf '%s' "$st" > "$STUBLOG/status"; echo "$ev"; sleep 0.3; done \
            < "$STUBLOG/events"; sleep 0.5 ;;
call) echo "call $2" >> "$STUBLOG/ubus.calls"
      st=$(cat "$STUBLOG/status"); [ -n "$st" ] || exit 4
      printf '{\n\t"status": "%s",\n\t"bss": [ "wlan0" ]\n}\n' "$st" ;;
esac''')
        rm = '{ "ubus.object.remove": {"id":2011872946,"path":"hostapd.wlan0"} }'
        add = '{ "ubus.object.add": {"id":-1383968478,"path":"hostapd.wlan0"} }'
        other = '{ "ubus.object.add": {"id":77,"path":"network.interface.wan"} }'
        (self.tmp / 'iw.out').write_text('Interface wlan0\n\tssid F50\n\ttype AP\n')
        for shell in self.each_shell():
            for events, lit in (
                    # wifi down: the object goes (iw still shows the SSID at that moment)
                    ([('', rm)], '0'),
                    # wifi up after a down: remove, add, remove, add (netifd's two passes); it ends on
                    ([('', rm), ('ENABLED', add), ('', rm), ('ENABLED', add)], '48'),
                    # an access point that hostapd set up but disabled is off; one still on its way (DFS) is on
                    ([('DISABLED', add)], '0'), ([('DFS', add)], '48')):
                with self.subTest(events=events):
                    self.reset()
                    self.conf.write_text('LED_TIMEOUT=0\n')
                    (self.tmp / 'status').write_text('ENABLED' if events[0][1] == rm else '')
                    (self.tmp / 'ubus.calls').unlink(missing_ok=True)
                    (self.tmp / 'events').write_text(''.join(f'{s}|{e}\n' for s, e in events + [(events[-1][0], other)]))
                    self.assertEqual(self.led(shell, 'f50', 'events').returncode, 0)
                    self.assertEqual(self.state()['keyboard-backlight'], lit)
                    # one status read at the start and one per hostapd event, none for the other object
                    self.assertEqual(len((self.tmp / 'ubus.calls').read_text().splitlines()), 1 + len(events))
        # Ubuntu has no ubus: systemd's hotspot unit lights it (ExecStartPost, ExecStopPost)
        (self.stubs / 'ubus').unlink()
        r = self.script(self.shells[0], BIN / 'mu300-led', 'events', MU300_SYSROOT=self.root, MU300_BIN=BIN,
                        PATH='%s:/usr/bin:/bin' % self.stubs)
        self.assertNotEqual(r.returncode, 0)
        unit = (BIN.parents[2] / 'etc/systemd/system/mu300-hotspot.service').read_text()
        self.assertIn('ExecStopPost=-/opt/mu300/bin/mu300-led wifi off', unit)

    def test_stale_siren_stops(self):
        # a siren the pid file does not name (left behind by a restart) stops by itself: two of them flashed
        # together, white lit throughout
        for shell in self.each_shell():
            self.reset()
            pid = self.root / 'run/mu300/led-siren.pid'
            self.led(shell, 'u30air', 'alarm', 'on')
            p = int(pid.read_text())
            pid.write_text('1\n')                                 # someone else's now
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    os.kill(p, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                os.kill(p, 15)
                self.fail('the stale siren kept running')
            pid.unlink()

    def test_vendor_ldo_switches_on_5_4(self):
        # the 5.4 kernel has no zte-ldoN LEDs: ZTE's zte_ldo_leds switches the same LDOs through vddcamaN_status
        for shell in self.each_shell():
            self.reset()
            for n in ('zte-ldo0', 'zte-ldo1', 'zte-ldo2'):
                shutil.rmtree(self.root / 'sys/class/leds' / n)
            vendor = self.root / 'sys/devices/platform/zte_ldo_leds'
            vendor.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                (vendor / f'vddcama{i}_status').write_text('0\n')
            self.led(shell, 'u30air', 'power', 'on')
            self.led(shell, 'u30air', 'data', '5g')
            (self.tmp / 'iw.out').write_text('Interface wlan0\n\ttype AP\n\tchannel 36 (5180 MHz)\n')
            self.led(shell, 'u30air', 'wifi', 'on')   # 5G: vddcama0; a 5 GHz hotspot: vddcama1 (white)
            self.assertEqual([(vendor / f'vddcama{i}_status').read_text().strip() for i in range(3)], ['1', '1', '0'])
            self.led(shell, 'u30air', 'sleep')
            self.assertEqual([(vendor / f'vddcama{i}_status').read_text().strip() for i in range(3)], ['0', '0', '0'])
            shutil.rmtree(self.root / 'sys/devices')
            self.setUp_leds()

    def test_alarm_siren(self):
        # too hot: red, white and blue take turns on the PMIC's LED until the alarm is off, then the LEDs are as before
        for shell in self.each_shell():
            for device in ('u30air', 'f50'):
                self.reset()
                self.conf.write_text('LED_TIMEOUT=0\n')
                self.led(shell, device, 'power', 'on')
                self.led(shell, device, 'data', 'on')
                pid = self.root / 'run/mu300/led-siren.pid'
                try:
                    self.assertEqual(self.led(shell, device, 'alarm', 'on').returncode, 0)
                    self.assertTrue(pid.exists())
                    self.led(shell, device, 'alarm', 'on')        # a second one starts no second siren
                    # one colour at a time: white (the U30 Air's green channel) never mixed in
                    want = {('255', '0', '0'), ('0', '255', '0'), ('0', '0', '255')}
                    seen = set()
                    deadline = time.time() + 4
                    while time.time() < deadline and not want <= seen:
                        s = self.state()
                        seen.add((s['sc27xx:red'], s['sc27xx:green'], s['sc27xx:blue']))
                        time.sleep(0.03)
                    self.assertLessEqual(want, seen, device)
                finally:
                    self.led(shell, device, 'alarm', 'off')
                self.assertFalse(pid.exists())
                time.sleep(0.4)                                   # a killed siren writes no more
                s = self.state()
                self.assertEqual(s['sc27xx:red'], '0')
                # the F50's RGB LED is its network light: blue (4G) again; the U30 Air's is its battery LED (80 %: white)
                self.assertEqual(s['sc27xx:blue'], '255' if device == 'f50' else '0', device)
                self.assertEqual(s['sc27xx:green'], '120' if device == 'u30air' else '0', device)
                self.assertEqual(self.led(shell, device, 'alarm', 'off').returncode, 0)   # off twice

    def test_missing_leds_and_bad_usage(self):
        for shell in self.each_shell():
            empty = self.tmp / 'empty'
            (empty / 'run/mu300').mkdir(parents=True, exist_ok=True)
            (empty / 'run/mu300/device').write_text('u30air\n')
            r = self.script(shell, BIN / 'mu300-led', 'data', 'on', MU300_SYSROOT=empty, MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0)  # an LED the device has not is skipped
            self.assertEqual(self.led(shell, 'u30air', 'blink').returncode, 2)


class WifiBand(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'hotspot.conf'

    def band(self, shell, *args):
        return self.script(shell, BIN / 'mu300-wifi-band', *args, MU300_HOTSPOT_CONF=self.conf)

    def test_toggle(self):
        for shell in self.each_shell():
            self.conf.write_text("SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=5\nCHANNEL=40\nCOUNTRY=TR\n")
            self.conf.chmod(0o600)
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            r = self.band(shell, 'toggle')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'hotspot: 2.4 GHz'), r.stderr)
            self.assertEqual(self.conf.read_text(),
                             "SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=2.4\nCHANNEL=auto\nCOUNTRY=TR\n")
            self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
            self.band(shell, 'toggle')
            self.assertIn('BAND=5\n', self.conf.read_text())
            self.band(shell, '2.4')
            self.band(shell, '2.4')
            self.assertEqual(self.conf.read_text().count('BAND='), 1)

    def test_missing_keys_and_file(self):
        for shell in self.each_shell():
            self.conf.write_text('SSID=x\nPSK=12345678\n')   # an old file without BAND/CHANNEL: 5 GHz
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            self.band(shell, 'toggle')
            self.assertEqual(self.conf.read_text(), 'SSID=x\nPSK=12345678\nBAND=2.4\nCHANNEL=auto\n')
            self.conf.unlink()
            self.assertEqual(self.band(shell, '5').returncode, 1)
            self.assertEqual(self.band(shell, '6').returncode, 2)


class Buttons(ShellTest):
    def test_actions(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'systemctl', 'logger', 'poweroff', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; [ "{name} $*" != "systemctl is-active --quiet mu300-hotspot" ]')
        # Every press lights the LEDs first (a key always shows life, whatever the state), then wakes mu300-power with
        # the key's name in the background (only wifi ends a charging boot; other keys count as power), which the
        # rest does not wait for: its order against the band and hotspot toggles is free.
        WP, WW = 'mu300-power wake power', 'mu300-power wake wifi'
        cases = [('116 short', [WP]),
                 ('138 short', [WW, 'mu300-wifi-band toggle']),
                 ('138 long', [WW, 'systemctl is-active --quiet mu300-hotspot', 'systemctl start mu300-hotspot']),
                 ('0 tick', None),   # a tick is no press: it must not wake, only let the LEDs time out (and the battery LED follow)
                 # held power: no mu300-power wake (the radios would come back on the way down); the shutdown marker
                 ('116 long', ['systemctl poweroff']),
                 ('115 short', [WP])]
        marker = self.tmp / 'run/mu300/power/shutdown'
        for shell in self.each_shell():
            for event, want in cases:
                (self.tmp / 'keys.in').write_text(event + '\n')
                (self.tmp / 'calls').unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                r = self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
                self.assertEqual(r.returncode, 0, r.stderr)
                calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
                calls = [c for c in calls if not c.startswith('logger')]
                if want is None:
                    self.assertEqual(calls, ['mu300-led tick'], event)
                else:
                    self.assertEqual(calls[0], 'mu300-led wake', event)
                    self.assertEqual(sorted(calls[1:]), sorted(want), event)
                    if 'systemctl is-active --quiet mu300-hotspot' in want:
                        self.assertLess(calls.index('systemctl is-active --quiet mu300-hotspot'),
                                        calls.index('systemctl start mu300-hotspot'))
                self.assertEqual(marker.exists(), event == '116 long', event)

    def test_held_power_in_the_charging_boot_starts(self):
        # the stock charger mode: hold power to switch the device on (FINDINGS 196); no shutdown, no marker
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'systemctl', 'logger', 'poweroff', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        st = self.tmp / 'run/mu300/power'
        st.mkdir(parents=True, exist_ok=True)
        (st / 'state').write_text('charging-boot\n')
        (self.tmp / 'keys.in').write_text('116 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            r = self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = [c for c in (self.tmp / 'calls').read_text().splitlines() if not c.startswith('logger')]
            self.assertEqual(sorted(calls), ['mu300-led wake', 'mu300-power wake'])
            self.assertFalse((st / 'shutdown').exists())

    def test_keys_light_the_leds_in_idle_without_any_daemon(self):
        """2026-10-07: the LEDs held dark by `mu300-led idle on`, the state file "active", and every key lit nothing.
        With the real mu300-led and a mu300-power that hangs, each press still lights them, at once."""
        import time
        root = self.tmp / 'root'
        for n in ('sc27xx:green', 'net_blue'):
            d = root / 'sys/class/leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n'); (d / 'max_brightness').write_text('255\n'); (d / 'trigger').write_text('none\n')
        (root / 'proc').mkdir(parents=True, exist_ok=True)
        (root / 'proc/uptime').write_text('500.00 0.00\n')
        (root / 'run/mu300/led').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300/device').write_text('u30air\n')
        (root / 'run/mu300/led/sc27xx:green').write_text('1\n'); (root / 'run/mu300/led/net_blue').write_text('1\n')
        (root / 'run/mu300/led/.idle').touch()
        (self.tmp / 'run/mu300/power').mkdir(parents=True, exist_ok=True)
        (self.tmp / 'run/mu300/power/state').write_text('active\n')
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        self.stub('mu300-power', 'echo "mu300-power $*" >> "$STUBLOG/calls"; sleep 3')   # no daemon, a slow wake
        for name in ('mu300-wifi-band', 'systemctl', 'logger'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('116 short\n138 short\n')
        for shell in self.each_shell():
            for n in ('sc27xx:green', 'net_blue'):
                (root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
            (self.tmp / 'calls').unlink(missing_ok=True)
            env = self.env(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'none')
            env['PATH'] = f'{self.stubs}{os.pathsep}{BIN}{os.pathsep}{env["PATH"]}'
            p = subprocess.Popen(shell + [str(BIN / 'mu300-buttons')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1.5)   # well inside the slow wake
            lit = {n: (root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in ('sc27xx:green', 'net_blue')}
            p.communicate(timeout=30)
            self.assertEqual(lit, {'sc27xx:green': '255', 'net_blue': '255'})
            calls = (self.tmp / 'calls').read_text()
            # the Wi-Fi press on a device held dark only wakes it: no band toggle
            self.assertNotIn('mu300-wifi-band', calls)
            self.assertIn('mu300-power wake power', calls); self.assertIn('mu300-power wake wifi', calls)

    def test_a_hung_direction_probe_is_bounded(self):
        """Ubuntu's systemctl is-active can hang: the hold's direction is decided within the probe deadline."""
        import time
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; [ "$1" != is-active ] || exec sleep 60')
        (self.tmp / 'keys.in').write_text('138 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            t0 = time.time()
            r = self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run', MU300_BUTTONS_PROBE_DEADLINE=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.time() - t0, 10)
            self.assertIn('systemctl start mu300-hotspot', (self.tmp / 'calls').read_text())   # no answer: not up

    def test_the_hold_marker_is_there_before_the_wake_of_the_same_press(self):
        """Review D2: the wake started by a Wi-Fi hold must find the user's marker already, or its restore sees the
        hotspot still up (or deactivating) and starts it again."""
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('mu300-power', 'echo "mu300-power $* marker=$([ -e "$MU300_RUN/mu300/power/hotspot-off" ] && echo yes || echo no)" >> "$STUBLOG/calls"')
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"')   # active: the hold turns it off
        (self.tmp / 'keys.in').write_text('138 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
            calls = (self.tmp / 'calls').read_text()
            self.assertIn('mu300-power wake wifi marker=yes', calls)
            self.assertIn('systemctl stop mu300-hotspot', calls)

    def test_a_hung_toggle_does_not_hold_the_next_press(self):
        """The hotspot toggle runs in the background under a deadline: the next press shows life at once."""
        import time
        self.stub('mu300-keys', 'echo "138 long"; sleep 1; echo "116 short"; sleep 2')
        for name in ('logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('mu300-led', 'echo "mu300-led $* $(date +%s)" >> "$STUBLOG/calls"')
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; [ "$1" != stop ] || { sleep 30; echo "stop done" >> "$STUBLOG/calls"; }')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            t0 = time.time()
            r = self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run', MU300_BUTTONS_DEADLINE=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.time() - t0, 15)
            calls = (self.tmp / 'calls').read_text()
            self.assertEqual(calls.count('mu300-led wake'), 2, calls)
            self.assertNotIn('stop done', calls)   # killed at its deadline

    def test_the_hotspot_toggle_leaves_a_marker_while_off(self):
        """mu300-power's wake and self-check leave a hotspot alone that the user turned off with the key."""
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('138 long\n')
        marker = self.tmp / 'run/mu300/power/hotspot-off'
        for shell in self.each_shell():
            self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"')   # active: the hold stops it
            self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
            self.assertTrue(marker.exists())
            self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; [ "$1" != is-active ]')
            self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
            self.assertFalse(marker.exists())

    def test_wifi_hold_while_asleep_wakes_and_toggles(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'systemctl', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'run/mu300/power').mkdir(parents=True)
        # asleep (state idle): a short press only wakes, a hold wakes and toggles the hotspot too; the charging boot
        # has no services to toggle, so both only wake; awake, both act.
        for state, short_acts, long_acts in (('idle', False, True), ('charging-boot', False, False), ('active', True, True)):
            (self.tmp / 'run/mu300/power/state').write_text(state + '\n')
            for shell in self.each_shell():
                for event, acts in (('138 short', short_acts), ('138 long', long_acts)):
                    (self.tmp / 'keys.in').write_text(event + '\n')
                    (self.tmp / 'calls').unlink(missing_ok=True)
                    r = self.script(shell, BIN / 'mu300-buttons', MU300_RUN=self.tmp / 'run')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    calls = (self.tmp / 'calls').read_text()
                    self.assertIn('mu300-power wake', calls, (state, event))
                    self.assertEqual(acts, ('mu300-wifi-band toggle' in calls or 'systemctl start' in calls
                                            or 'systemctl stop' in calls or 'systemctl is-active' in calls), (state, event))


class TreeKill(ShellTest):
    """tree_kill (mu300-power, mu300-buttons, mobile-data): a process seen in the tree by one look and gone from it by
    the next (reparented: its parent exited meanwhile) is killed too - the union of every look, not the last one."""

    @staticmethod
    def func(path):
        m = re.search(r'^tree_kill\(\) \{.*?^\}$', path.read_text(), re.S | re.M)
        assert m, path
        return m.group(0)

    def test_every_process_seen_is_killed(self):
        # the first look sees 100 -> 101 -> 102; every later one finds 102 reparented to 1 (out of the tree)
        fake = ('proc_table() { n=$(cat "$STUBLOG/n" 2>/dev/null || echo 0); echo $((n + 1)) > "$STUBLOG/n"\n'
                '  if [ "$n" = 0 ]; then printf "100 1\\n101 100\\n102 101\\n"; else printf "100 1\\n101 100\\n102 1\\n"; fi; }\n'
                'kill() { echo "$*" >> "$STUBLOG/kills"; }\n')
        bash = shutil.which('bash')
        for name in ('mu300-power', 'mu300-buttons', 'mobile-data'):
            if name == 'mobile-data' and not bash:
                continue
            for shell in ([[bash]] if name == 'mobile-data' else self.each_shell()):
                for f in ('n', 'kills'):
                    (self.tmp / f).unlink(missing_ok=True)
                r = self.sh(shell, self.func(BIN / name) + '\n' + fake + 'tree_kill 100\n')
                self.assertEqual(r.returncode, 0, (name, r.stderr))
                kills = (self.tmp / 'kills').read_text().split('\n')
                for pid in ('100', '101', '102'):
                    self.assertIn(f'-KILL {pid}', kills, (name, shell))


class WcnReopen(ShellTest):
    """wcn-reopen (the WCN chip was reset, #94): Wi-Fi reopened in whichever mode it was in."""
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'sys/class/net/wlan0').mkdir(parents=True)
        (self.root / 'run').mkdir()
        # systemctl: is-active answers from $STUBLOG/active-UNIT; every other call is recorded
        self.stub('systemctl', 'if [ "$1" = -q ] && [ "$2" = is-active ]; then [ -e "$STUBLOG/active-$3" ]; exit; fi\n'
                               'echo "systemctl $*" >> "$STUBLOG/calls"')
        self.stub('ip', 'case "$*" in "link show wlan0") cat "$STUBLOG/link" ;; *) echo "ip $*" >> "$STUBLOG/calls" ;; esac')

    def run_reopen(self, shell, hotspot=False, client=False, up=True):
        for f in ('calls', 'active-mu300-hotspot.service'):
            (self.tmp / f).unlink(missing_ok=True)
        (self.root / 'run/mu300-wifi-client.active').unlink(missing_ok=True)
        if hotspot:
            (self.tmp / 'active-mu300-hotspot.service').write_text('')
        if client:
            (self.root / 'run/mu300-wifi-client.active').write_text('')
        flags = 'BROADCAST,MULTICAST,UP,LOWER_UP' if up else 'BROADCAST,MULTICAST'
        (self.tmp / 'link').write_text(f'5: wlan0: <{flags}> mtu 1500 qdisc mq state UP\n')
        r = self.script(shell, BIN / 'wcn-reopen', MU300_SYSROOT=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.tmp / 'calls'
        return calls.read_text().splitlines() if calls.exists() else []

    def test_hotspot_is_restarted(self):
        for shell in self.each_shell():
            self.assertEqual(self.run_reopen(shell, hotspot=True), ['systemctl restart mu300-hotspot.service'])

    def test_a_client_gets_wlan0_down_and_up_and_joins_again(self):
        # restarting the client alone left the chip off (wifi-client never takes wlan0 down): the watchdog rebooted
        for shell in self.each_shell():
            self.assertEqual(self.run_reopen(shell, client=True),
                             ['systemctl stop mu300-wifi-client.service', 'ip link set wlan0 down',
                              'ip link set wlan0 up', 'systemctl start mu300-wifi-client.service'])

    def test_neither_up_powers_the_chip_on_and_starts_nothing(self):
        for shell in self.each_shell():
            with self.subTest(wlan0='up'):
                self.assertEqual(self.run_reopen(shell), ['ip link set wlan0 down', 'ip link set wlan0 up'])
            with self.subTest(wlan0='down'):
                self.assertEqual(self.run_reopen(shell, up=False),
                                 ['ip link set wlan0 down', 'ip link set wlan0 up', 'ip link set wlan0 down'])

    def test_the_udev_rule_starts_the_service(self):
        rule = (TOP / 'rootfs/overlay/etc/udev/rules.d/70-mu300-wcn.rules').read_text()
        self.assertIn('ENV{EVENT}=="FW_ERROR", RUN+="/bin/systemctl --no-block start mu300-wcn-reopen.service"', rule)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-wcn-reopen.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/wcn-reopen\n', unit)


class OpenwrtWcnHotplug(ShellTest):
    """OpenWrt's hotplug.d/misc/10-mu300-wcn: one "wifi down; wifi up" per WCN reset, however many FW_ERROR events
    (kanoqwq/mu300-linux@23361ce: a single recovery owner)."""
    HOOK = TOP / 'openwrt/overlay/etc/hotplug.d/misc/10-mu300-wcn'

    def setUp(self):
        super().setUp()
        self.stub('logger', ':')
        self.stub('wifi', 'echo "wifi $1" >> "$STUBLOG/calls"; sleep 1')

    def fire(self, shell, **env):
        return self.script(shell, self.HOOK, ACTION='change', SOURCE='unisocwl', EVENT='FW_ERROR',
                           MU300_WCN_LOCK=self.tmp / 'lock', **env)

    def wait_calls(self, n):
        import time
        calls = self.tmp / 'calls'
        for _ in range(300):
            if calls.exists() and len(calls.read_text().split()) >= 2 * n and not (self.tmp / 'lock').exists():
                break
            time.sleep(0.05)
        time.sleep(2.5)       # a second reopen that got in would have written by now
        return calls.read_text().splitlines() if calls.exists() else []

    def test_other_events_do_nothing(self):
        for shell in self.each_shell():
            r = self.script(shell, self.HOOK, ACTION='change', SOURCE='unisocwl', EVENT='OTHER')
            self.assertEqual(r.returncode, 0)
        import time
        time.sleep(1.5)
        self.assertFalse((self.tmp / 'calls').exists())

    def test_overlapping_events_reopen_once(self):
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            for _ in range(3):
                self.fire(shell)
            self.assertEqual(self.wait_calls(1), ['wifi down', 'wifi up'])

    def test_a_dead_holder_does_not_block_the_next_reset(self):
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            (self.tmp / 'lock').mkdir(exist_ok=True)
            (self.tmp / 'lock' / 'pid').write_text('999999\n')
            self.fire(shell)
            self.assertEqual(self.wait_calls(1), ['wifi down', 'wifi up'])
            self.assertFalse((self.tmp / 'lock').exists())


class ThermalGuard(ShellTest):
    """thermal-guard's LED alarm (one round against a fake /: MU300_SYSROOT)."""
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        led = self.bin / 'mu300-led'
        led.write_text('#!/bin/sh\necho "mu300-led $*" >> "$STUBLOG/calls"\n')
        led.chmod(0o755)
        shutil.copy(BIN / 'mu300-device', self.bin / 'mu300-device')
        self.device('u30air')
        self.stub('logger', ':')
        self.stub('poweroff', 'echo poweroff >> "$STUBLOG/calls"')

    def device(self, name):
        (self.root / 'run/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'run/mu300/device').write_text(name + '\n')

    def round(self, shell, soc, battery=None, trips=False):
        z = self.root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'temp').write_text(f'{soc}\n')
        if trips:
            (z / 'trip_point_0_temp').write_text('95000\n')
        if battery is not None:
            b = self.root / 'sys/class/power_supply/battery'
            b.mkdir(parents=True, exist_ok=True)
            (b / 'type').write_text('Battery\n')
            (b / 'temp').write_text(f'{battery}\n')
        (self.tmp / 'calls').unlink(missing_ok=True)
        r = self.script(shell, BIN / 'thermal-guard', MU300_SYSROOT=self.root, MU300_BIN=self.bin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []

    def test_alarm_on_and_off(self):
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            self.assertEqual(self.round(shell, 60000, 300), [])
            self.assertEqual(self.round(shell, 86000, 300), ['mu300-led alarm on'])
            self.assertEqual(self.round(shell, 90000, 300), [])                # on already
            self.assertEqual(self.round(shell, 80000, 300), [])                # not cool enough yet
            self.assertEqual(self.round(shell, 74000, 300), ['mu300-led alarm off'])
            self.assertEqual(self.round(shell, 60000, 510), ['mu300-led alarm on'])   # the battery alone
            self.assertEqual(self.round(shell, 60000, 460), [])
            self.assertEqual(self.round(shell, 60000, 440), ['mu300-led alarm off'])

    def test_f50_battery_is_ignored(self):
        # the F50 has no battery, but mainline's fuel gauge reports one at 75.0 C from an open NTC input: the
        # alarm went on at every boot and never off (v2026.10.06)
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('f50')
            self.assertEqual(self.round(shell, 46000, 750), [])
            self.assertEqual(self.round(shell, 86000, 750), ['mu300-led alarm on'])    # the SoC still counts
            self.assertEqual(self.round(shell, 74000, 750), ['mu300-led alarm off'])

    def kernel(self, release):
        (self.root / 'proc/sys/kernel').mkdir(parents=True, exist_ok=True)
        (self.root / 'proc/sys/kernel/osrelease').write_text(release + '\n')

    VENDOR = '5.4.254-android12-9-gb50db5b6224c'

    def test_vendor_kernel_is_asked_like_any_other(self):
        # issue #229: a 5.4 whose sprd_cpu_cooling found no thermal zone ("get thermal zone ank0-thmzone failed" ...)
        # throttled nothing, and the guard - exempt by the release - only ran the alarm until the unit reset. The
        # release decides nothing now: a 5.4 without a bound zone is capped and powered off at CRIT like mainline
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            for release in (self.VENDOR, '7.2.10'):
                with self.subTest(release=release):
                    shutil.rmtree(self.root, ignore_errors=True)
                    (self.tmp / 'log').unlink(missing_ok=True)
                    self.device('u30air')
                    self.kernel(release)
                    self.assertEqual(self.round(shell, 110000, trips=True), ['poweroff', 'mu300-led alarm on'])
                    self.assertIn(f'no kernel thermal zone throttles the CPUs (kernel {release})', self.log())

    def test_vendor_kernel_without_a_bound_zone_caps(self):
        for shell in self.each_shell():
            self.mainline(90000)
            self.kernel(self.VENDOR)
            # 5.4's cpufreq cooling devices are "thermal-cpufreq-N"; here none is bound to the zone with the trips
            shutil.rmtree(self.root / 'sys/class/thermal')
            for i, cpu in enumerate((0, 4, 7)):
                self.cooling(i, f'thermal-cpufreq-{i}')
            self.zone(0, 'soc-thmzone', 90000, [('passive', 70000), ('passive', 85000), ('critical', 110000)])
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    def test_vendor_kernel_with_its_zone_bound_only_watches(self):
        # where 5.4's sprd_cpu_cooling does bind (the earlier measurement, FINDINGS 18), the kernel throttles: the
        # guard hands over exactly as on mainline with mu300_thermal; its own power-off at CRIT stays
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            self.mainline(90000)
            self.kernel(self.VENDOR)
            shutil.rmtree(self.root / 'sys/class/thermal')
            for i in range(3):
                self.cooling(i, f'thermal-cpufreq-{i}')
            self.zone(0, 'soc-thmzone', 90000, [('passive', 70000), ('passive', 85000), ('critical', 110000)],
                      (0, 1, 2))
            (self.tmp / 'log').unlink(missing_ok=True)
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.assertIn('soc-thmzone throttles the CPUs: watching only', self.log())
            self.assertEqual(self.round(shell, 105000), ['poweroff'])

    # --- the stock virtual zone on mainline (mu300_thermal): the kernel throttles, this guard only watches
    def cooling(self, n, kind, state=0):
        d = self.root / f'sys/class/thermal/cooling_device{n}'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'type').write_text(kind + '\n')
        (d / 'cur_state').write_text(f'{state}\n')
        (d / 'max_state').write_text('9\n')
        return d

    def zone(self, n, kind, temp, trips=(), cdevs=(), mode='enabled'):
        """thermal_zoneN of type KIND with TRIPS [(type, temp)] and the cooling devices CDEVS bound (cdevK links)."""
        z = self.root / f'sys/class/thermal/thermal_zone{n}'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'type').write_text(kind + '\n')
        (z / 'temp').write_text(f'{temp}\n')
        (z / 'mode').write_text(mode + '\n')
        for i, (tt, tv) in enumerate(trips):
            (z / f'trip_point_{i}_type').write_text(tt + '\n')
            (z / f'trip_point_{i}_temp').write_text(f'{tv}\n')
        for i, c in enumerate(cdevs):
            (z / f'cdev{i}').symlink_to(f'../cooling_device{c}')
        return z

    def soc_zone(self, temp, cdevs=(0, 1, 2), mode='enabled'):
        """the stock soc-thmzone as mu300_thermal brings it back: passive 70 and 85 C, critical 110 C, the three
        cpufreq cooling devices bound through the cooling maps (thermal_zone0..2 are physical zones without trips)"""
        for i, kind in enumerate(('lit0-thmzone', 'big7-thmzone', 'gpu-thmzone')):
            self.zone(i, kind, temp)
        for i, cpu in enumerate((0, 4, 7)):
            self.cooling(i, f'cpufreq-cpu{cpu}')
        return self.zone(3, 'soc-thmzone', temp, [('passive', 70000), ('passive', 85000), ('critical', 110000)],
                         cdevs, mode)

    def mainline(self, temp, **zone):
        shutil.rmtree(self.root, ignore_errors=True)
        self.device('f50')
        self.kernel('7.2.10')
        freqs = [614400, 936000, 1105000, 1404000, 2002000]
        self.pols = [self.policy(p, freqs, 2002000) for p in ('policy0', 'policy4', 'policy7')]
        if zone is not None:
            self.soc_zone(temp, **zone)

    def maxes(self):
        return [(p / 'scaling_max_freq').read_text().strip() for p in self.pols]

    def log(self):
        return (self.tmp / 'log').read_text() if (self.tmp / 'log').exists() else ''

    def test_kernel_cooling_bound_to_the_stock_zone_hands_frequency_control_over(self):
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            self.mainline(90000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])    # hot: the alarm, no capping
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.assertIn('soc-thmzone', self.log())
            self.assertIn('watching', self.log())
            self.assertEqual(self.round(shell, 105000), ['poweroff'])             # our 105 C power-off stays
            self.assertEqual(self.maxes(), ['2002000'] * 3)

    def test_mainline_without_the_zone_caps_and_powers_off(self):
        for shell in self.each_shell():
            self.mainline(90000)
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.assertEqual(self.round(shell, 105000), ['poweroff'])

    def test_a_zone_whose_reading_cannot_be_trusted_keeps_this_guard_capping(self):
        for shell in self.each_shell():
            self.mainline(90000)                                                  # bound, but the zone does not read
            (self.root / 'sys/class/thermal/thermal_zone3/temp').unlink()
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.mainline(90000)                                                  # reads, but not a number
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.mainline(90000)                                                  # stale: far below the die sensors
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('40000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.mainline(90000)                                                  # within 15 C of the hottest: trusted
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('80000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.mainline(90000)                                                  # passive trips above HOT: not ours
            z = self.root / 'sys/class/thermal/thermal_zone3'
            (z / 'trip_point_0_temp').write_text('95000\n')
            (z / 'trip_point_1_temp').write_text('100000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    def test_a_cluster_left_out_of_the_zone_keeps_this_guard_capping(self):
        for shell in self.each_shell():
            self.mainline(90000, cdevs=(0, 1))                                    # cpufreq-cpu7 not bound
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.mainline(90000, mode='disabled')                                 # the zone is off
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.mainline(90000)
            for i in range(3):                                                    # no cpufreq cooling device at all
                shutil.rmtree(self.root / f'sys/class/thermal/cooling_device{i}')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    def test_handoff_lifts_the_caps_this_guard_set_and_the_zone_gone_caps_again(self):
        for shell in self.each_shell():
            self.mainline(90000, cdevs=())
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')          # module not loaded yet
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1105000\n')
            self.round(shell, 90000)                                              # hot: one step down everywhere
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.soc_zone(80000)                                                  # mu300_thermal loaded, zone bound
            self.round(shell, 80000)                                              # neither cool nor hot
            # our caps go (the kernel holds its own, as a QoS limit under scaling_max_freq), the profile's stays
            self.assertEqual(self.maxes(), ['1105000', '2002000', '2002000'])
            self.assertFalse((self.root / 'run/mu300/thermal-capped').exists())
            self.round(shell, 90000)                                              # hot under the kernel: untouched
            self.assertEqual(self.maxes(), ['1105000', '2002000', '2002000'])
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')          # rmmod
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['936000', '1404000', '1404000'])

    def test_a_restarted_guard_lifts_its_old_cap_on_the_handoff_too(self):
        # the marker outlives a restart: the cap of a run before the module loaded does not stick for ever
        for shell in self.each_shell():
            self.mainline(80000)
            for p in self.pols:
                (p / 'scaling_max_freq').write_text('1404000\n')
            (self.root / 'run/mu300/thermal-capped').write_text('')
            self.round(shell, 80000)
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.mainline(80000)                                                  # no marker: another cap is not ours
            for p in self.pols:
                (p / 'scaling_max_freq').write_text('1404000\n')
            self.round(shell, 80000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    # --- the backstop: the kernel throttles, but the SoC climbs on (6.18.56's step_wise under 8 awk loops: 98 C in
    # three minutes with the states at 1-3 of 9, FINDINGS 166)
    def test_backstop_caps_the_a76_clusters_on_top_of_the_kernel_from_92_c(self):
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            self.mainline(91000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.round(shell, 91000)                                              # under the backstop: watching
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.mainline(93000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.assertEqual(self.round(shell, 93000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['2002000', '1404000', '1404000'])     # policy0 (A55) left alone
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.assertIn('soc-thmzone does not hold the SoC (93000 mC)', self.log())
            self.round(shell, 93000)                                              # a step a round, logged once
            self.assertEqual(self.maxes(), ['2002000', '1105000', '1105000'])
            self.assertEqual(self.log().count('does not hold'), 1)

    def test_backstop_caps_every_cluster_from_95_c_and_powers_off_at_crit_still(self):
        for shell in self.each_shell():
            self.mainline(96000)
            self.round(shell, 96000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.assertEqual(self.round(shell, 105000), ['poweroff'])

    def heat(self, temp):
        """every zone at TEMP (round() sets thermal_zone0 only)"""
        for t in (self.root / 'sys/class/thermal').glob('thermal_zone*/temp'):
            t.write_text(f'{temp}\n')

    def test_backstop_lifts_its_caps_at_hot_a_step_a_round_never_above_the_ceiling(self):
        for shell in self.each_shell():
            self.mainline(96000)
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1404000\n')
            self.round(shell, 96000)
            self.round(shell, 96000)
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.heat(88000); self.round(shell, 88000)            # between HOT and the backstop: held
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.heat(85000); self.round(shell, 85000)
            self.assertEqual(self.maxes(), ['1404000', '1404000', '1404000'])
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.heat(84000); self.round(shell, 84000)
            self.assertEqual(self.maxes(), ['1404000', '2002000', '2002000'])     # the profile's ceiling stays
            self.assertFalse((self.root / 'run/mu300/thermal-capped').exists())
            self.heat(84000); self.round(shell, 84000)            # no marker: nothing more
            self.assertEqual(self.maxes(), ['1404000', '2002000', '2002000'])

    def policy(self, name, freqs, cur_max):
        p = self.root / 'sys/devices/system/cpu/cpufreq' / name
        p.mkdir(parents=True, exist_ok=True)
        (p / 'scaling_available_frequencies').write_text(' '.join(str(f) for f in freqs) + ' \n')
        (p / 'cpuinfo_max_freq').write_text(f'{max(freqs)}\n')
        (p / 'scaling_max_freq').write_text(f'{cur_max}\n')
        return p

    def test_cooldown_does_not_raise_a_policy_above_the_mu300_cpu_ceiling(self):
        # mu300-cpu's "saving" profile caps a cluster and records the cap in /run/mu300/cpu-ceiling; on cool-down
        # thermal-guard steps scaling_max up a notch at a time but never past that ceiling, so the profile stays.
        freqs = [614400, 768000, 936000, 1105000, 1228800, 1404000, 2002000]
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            p = self.policy('policy0', freqs, 936000)   # capped below hw max (2002000)
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1105000\n')
            self.round(shell, 60000)                     # cool: one step up, but clamped to the ceiling
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '1105000')
            self.round(shell, 60000)                     # at the ceiling now: no further rise
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '1105000')

    def test_cooldown_without_a_ceiling_rises_to_the_hardware_maximum(self):
        freqs = [614400, 936000, 1105000, 2002000]
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            p = self.policy('policy0', freqs, 936000)
            for _ in range(5):
                self.round(shell, 60000)
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '2002000')


class Nfc(ShellTest):
    """mu300-nfc against a fake FM11NT08: 1 KiB behind a stub i2ctransfer, which answers only while a stub gpioset
    holds line 190 low, as the tag does."""
    EEPROM = r'''
import os, sys
mem = os.path.join(os.environ['STUBLOG'], 'tag.bin')
data = bytearray(open(mem, 'rb').read())
args = [a for a in sys.argv[1:] if a != '-y']
bus, op = args[0], args[1]
if not os.path.exists(os.path.join(os.environ['STUBLOG'], 'held')) or bus != '2' or not op.endswith('@0x57'):
    sys.exit('Error: Sending messages failed: Remote I/O error')
n = int(op[1:op.index('@')])
addr = int(args[2], 16) << 8 | int(args[3], 16)
if op.startswith('w') and len(args) > 4 and args[4].startswith('r'):
    print(' '.join('0x%02x' % b for b in data[addr:addr + int(args[4][1:])]))
elif op.startswith('w'):
    body = [int(x, 16) for x in args[4:]]
    # NDEF pages of 16 bytes; the configuration byte on its own
    assert len(body) == n - 2 and (len(body) <= 16 and addr % 16 == 0 and addr >= 16 or (addr, len(body)) == (0x3bf, 1)), args
    data[addr:addr + len(body)] = bytes(body)
    open(mem, 'wb').write(bytes(data))
    open(os.path.join(os.environ['STUBLOG'], 'writes'), 'a').write('%x\\n' % addr)
'''
    # what ZTE's firmware leaves there: UID, capability container (NDEF, 872 bytes), a Wi-Fi record
    ZTE = bytes.fromhex('1df4bcdd99030012 88b20000e1106d00'.replace(' ', ''))

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        chip = self.tmp / 'devices/platform/soc/64170000.gpio/gpiochip4'
        chip.mkdir(parents=True)
        (self.root / 'sys/bus/gpio/devices').mkdir(parents=True)
        (self.root / 'sys/bus/gpio/devices/gpiochip4').symlink_to(chip)
        (self.tmp / 'eeprom.py').write_text(self.EEPROM)
        self.stub('i2ctransfer', 'exec python3 "$STUBLOG/eeprom.py" "$@"')
        # libgpiod 2: holds the line until killed
        self.stub('gpioset', '[ "$1" = --help ] && { echo "  -c, --chip"; exit 0; }; '
                             '[ "$*" = "-c gpiochip4 190=0" ] || exit 1; touch "$STUBLOG/held"; '
                             'trap \'rm -f "$STUBLOG/held"; exit 0\' TERM; while :; do sleep 0.05; done')
        self.conf = self.tmp / 'hotspot.conf'
        self.conf.write_text('SSID=U30 AIR\nPSK=secret pass!\nBAND=5\n')

    def blank(self, ndef=b'\x03\x00\xfe'):
        (self.tmp / 'tag.bin').write_bytes(self.ZTE + ndef + bytes(1024 - 16 - len(ndef)))
        (self.tmp / 'writes').unlink(missing_ok=True)

    def nfc(self, shell, *args, device='u30air'):
        (self.root / 'run/mu300/device').write_text(device + '\n')
        return self.script(shell, BIN / 'mu300-nfc', *args, MU300_SYSROOT=self.root, MU300_BIN=BIN,
                           MU300_HOTSPOT_CONF=self.conf)

    def writes(self):
        w = self.tmp / 'writes'
        return w.read_text().split() if w.exists() else []

    def test_wifi_record_is_zte_s(self):
        # byte for byte what the stock firmware writes (WSC credential, WPA2-PSK, AES)
        for shell in self.each_shell():
            self.blank()
            r = self.nfc(shell, 'wifi')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'wifi: U30 AIR (with its password)'), r.stderr)
            self.assertNotIn('secret', r.stdout + r.stderr)
            ssid, key = b'U30 AIR', b'secret pass!'
            cred = (b'\x10\x45\x00' + bytes([len(ssid)]) + ssid + b'\x10\x03\x00\x02\x00\x20\x10\x0f\x00\x02\x00\x08'
                    + b'\x10\x27\x00' + bytes([len(key)]) + key + b'\x10\x20\x00\x06' + bytes(6)
                    + b'\x10\x49\x00\x06\x00\x37\x2a\x00\x01\x20')
            payload = b'\x10\x0e\x00' + bytes([len(cred)]) + cred
            rec = b'\xd2\x17' + bytes([len(payload)]) + b'application/vnd.wfa.wsc' + payload
            want = b'\x03' + bytes([len(rec)]) + rec + b'\xfe'
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(data[16:16 + len(want)], want)
            self.assertEqual(data[:16], self.ZTE)                     # UID and capability container untouched
            self.assertFalse((self.tmp / 'held').exists())            # the line is let go
            # again: nothing changed, nothing written
            (self.tmp / 'writes').unlink()
            self.assertEqual(self.nfc(shell, 'wifi').returncode, 0)
            self.assertEqual(self.writes(), [])

    def test_url_text_clear(self):
        for shell in self.each_shell():
            self.blank()
            for args, shown in ((('url', 'https://github.com/dikeckaan/mu300-linux'),
                                 'url: https://github.com/dikeckaan/mu300-linux'),
                                (('url', 'tel:+905551112233'), 'url: tel:+905551112233'),
                                (('url', 'geo:41.0,29.0'), 'url: geo:41.0,29.0'),
                                (('text', 'Merhaba, şifre kutunun altında'), 'text: Merhaba, şifre kutunun altında'),
                                (('clear',), 'empty')):
                r = self.nfc(shell, *args)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, shown), r.stderr)
                self.assertEqual(self.nfc(shell).stdout.strip(), shown)
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(data[16:19], b'\x03\x00\xfe')

    def test_long_url(self):
        # over 255 bytes: a long record and a three-byte TLV length
        for shell in self.each_shell():
            self.blank()
            url = 'https://example.com/' + 'x' * 400
            r = self.nfc(shell, 'url', url)
            self.assertEqual(r.stdout.strip(), 'url: ' + url, r.stderr)
            data = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual((data[16], data[17]), (0x03, 0xff))
            r = self.nfc(shell, 'url', 'https://example.com/' + 'x' * 900)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('too long', r.stderr)

    def test_sync_keeps_what_the_user_wrote(self):
        for shell in self.each_shell():
            self.blank()
            self.conf.write_text('SSID=U30 AIR\nPSK=secret pass!\nBAND=5\n')
            self.assertEqual(self.nfc(shell, 'sync').stdout.strip(), 'wifi: U30 AIR (with its password)')
            self.conf.write_text('SSID=Ev\nPSK=another one\n')
            self.assertEqual(self.nfc(shell, 'sync').stdout.strip(), 'wifi: Ev (with its password)')
            self.nfc(shell, 'url', 'https://example.com/')
            (self.tmp / 'writes').unlink()
            r = self.nfc(shell, 'sync')
            self.assertIn('left as it is', r.stdout)
            self.assertEqual(self.writes(), [])
            self.assertEqual(self.nfc(shell).stdout.strip(), 'url: https://example.com/')

    def test_on_off(self):
        # ZTE's NFC switch: bit 5 of the configuration byte 0x3bf (set: phones get nothing); nothing else changes
        for shell in self.each_shell():
            self.blank()
            data = bytearray((self.tmp / 'tag.bin').read_bytes())
            data[0x3b0:0x3c0] = bytes.fromhex('0578f057806002 1e0000009e44000420'.replace(' ', ''))
            (self.tmp / 'tag.bin').write_bytes(bytes(data))
            r = self.nfc(shell)
            self.assertIn('NFC is off', r.stdout)
            r = self.nfc(shell, 'on')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'NFC on'), r.stderr)
            after = (self.tmp / 'tag.bin').read_bytes()
            self.assertEqual(after[0x3b0:0x3c0], bytes.fromhex('0578f0578060021e0000009e44000400'))
            self.assertEqual(after[:0x3bf], bytes(data[:0x3bf]))
            self.assertNotIn('NFC is off', self.nfc(shell).stdout)
            self.assertEqual(self.nfc(shell, 'off').stdout.strip(), 'NFC off')
            self.assertEqual((self.tmp / 'tag.bin').read_bytes()[0x3bf], 0x20)

    def test_refusals(self):
        for shell in self.each_shell():
            self.blank()
            r = self.nfc(shell, 'wifi', device='f50')
            self.assertEqual(r.returncode, 1)
            self.assertIn('only the U30 Air', r.stderr)
            # the V50's tag is on another reset line: not driven, and the tag is not touched (FINDINGS 95)
            r = self.nfc(shell, 'wifi', device='v50')
            self.assertEqual(r.returncode, 1)
            self.assertIn("the V50's NFC tag is not supported yet", r.stderr)
            self.assertEqual(self.writes(), [])
            self.assertEqual(self.nfc(shell, 'blink').returncode, 2)
            self.assertEqual(self.nfc(shell, 'url').returncode, 1)
            # no capability container: not written
            (self.tmp / 'tag.bin').write_bytes(bytes(1024))
            r = self.nfc(shell, 'url', 'https://example.com/')
            self.assertEqual(r.returncode, 1)
            self.assertIn('capability container', r.stderr)
            self.assertEqual(self.writes(), [])


class Ttl(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')
        self.stub('modprobe', 'exit 1')   # no act_pedit (the 5.4 kernel): the nft backend; test_ttl.TtlTc has tc
        # nft records its arguments, and the ruleset it is given on stdin
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = -f ] && cat >> "$STUBLOG/nft.in"; '
                         '[ "$1" = list ] && exit 1; exit 0')

    def ttl(self, shell, *args):
        return self.script(shell, BIN / 'mu300-ttl', *args, MU300_TTL_CONF=self.conf)

    def reset(self):
        for f in ('nft.args', 'nft.in'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)

    def test_set(self):
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'TTL=64\n')
            rules = (self.tmp / 'nft.in').read_text()
            self.assertIn('table inet mu300_ttl', rules)
            self.assertIn('oifname "sipa_eth*" ip ttl set 64', rules)
            self.assertIn('oifname "sipa_eth*" ip6 hoplimit set 64', rules)
            self.assertIn('fixed at 64', r.stdout)

    def test_invalid_values_change_nothing(self):
        for shell in self.each_shell():
            for v in ('0', '256', 'abc', '-1', '6 4', ''):
                self.reset()
                r = self.ttl(shell, 'set', v)
                self.assertEqual(r.returncode, 2, v)
                self.assertFalse(self.conf.exists(), v)
                self.assertFalse((self.tmp / 'nft.in').exists(), v)

    def test_off_and_apply(self):
        for shell in self.each_shell():
            self.reset()
            self.ttl(shell, 'set', '128')
            (self.tmp / 'nft.in').unlink()
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertFalse(self.conf.exists())
            self.assertIn('delete table inet mu300_ttl', (self.tmp / 'nft.args').read_text())
            self.assertFalse((self.tmp / 'nft.in').exists())  # no rule without a setting
            self.assertIn('not changed', r.stdout)
            # a hand-edited file with junk sets nothing
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=999\n')
            self.ttl(shell, 'apply')
            self.assertFalse((self.tmp / 'nft.in').exists())

    def test_not_root(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.ttl(shell).returncode, 0)  # status works without root



class UsbReset(ShellTest):
    """mu300-usb-reset (K67): no rebind in host role, configfs mounted when init unmounted it, --fast-run."""

    def setUp(self):
        super().setUp()
        self.gadget = self.tmp / 'gadget'
        self.gadget.mkdir()
        (self.gadget / 'UDC').write_text('5e100000.usb\n')
        self.role = self.tmp / 'role'
        self.role.write_text('device\n')
        self.hook = self.tmp / 'hook'
        self.hook.write_text('#!/bin/sh\necho "hook $ACTION $INTERFACE" >> "$STUBLOG/calls"\n'
                             '[ "$MU300_USB_SKIP_REBIND" = 1 ] || echo queued-rebind >> "$STUBLOG/calls"\n')
        self.hook.chmod(0o755)
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        for n in ('sleep', 'usleep', 'ip', 'logger', 'mount'):
            self.stub(n, f'echo "{n} $*" >> "$STUBLOG/calls"')

    def reset(self, shell, *args, **env):
        e = dict(MU300_GADGET=self.gadget, MU300_USB_ROLE=self.role, MU300_USB_HOOK=self.hook,
                 MU300_RUN_DIR=self.run_dir, PATH=f'{self.stubs}:/usr/bin:/bin')
        e.update(env)
        return self.script(shell, BIN / 'mu300-usb-reset', *args, **e)

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def test_host_role_exits_without_touching_udc(self):
        self.role.write_text('host\n')
        for shell in self.each_shell():
            for arg in ('--fast-run', '--run', '--ready', '--if-no-lease'):
                r = self.reset(shell, arg)
                self.assertEqual(0, r.returncode, r.stderr)
                self.assertEqual('5e100000.usb\n', (self.gadget / 'UDC').read_text(), arg)
                self.assertEqual('', self.calls(), arg)

    def test_fast_run_rebinds_in_100_ms_and_hands_off_the_lan(self):
        # UDC is a plain file: the unbind is seen at once, the rebind writes the name back
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            (self.gadget / 'UDC').write_text('5e100000.usb\n')
            r = self.reset(shell, '--fast-run')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('5e100000.usb', (self.gadget / 'UDC').read_text().strip())
            calls = self.calls()
            self.assertIn('usleep 100000\n', calls)
            self.assertNotIn('sleep 2', calls.replace('usleep', ''))
            self.assertIn('hook ifup lan\n', calls)
            self.assertNotIn('queued-rebind', calls, 'USB reset recovery queued another rebind')
            self.assertIn('mu300-usb', calls)       # logger tag
            self.assertTrue(float((self.run_dir / 'mu300-usb-rebind-done').read_text().split()[0]) >= 0)

    def test_mounts_configfs_when_init_unmounted_it(self):
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            r = self.reset(shell, '--fast-run', MU300_GADGET=self.tmp / 'nogadget')
            self.assertEqual(1, r.returncode)
            self.assertIn('mount -t configfs configfs /sys/kernel/config', self.calls())
            self.assertIn('no gadget', r.stderr)

    def test_ready_detaches_a_fast_run(self):
        for shell in self.each_shell():
            r = self.reset(shell, '--ready')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertNotIn('re-enumerating', r.stdout)

    def test_lease_check_kept(self):
        # --if-no-lease still exits quietly when a lease file exists (the interim, until the K12 gate)
        lease = self.tmp / 'leases'
        lease.write_text('1 aa:bb 10.0.0.2 h *\n')
        for shell in self.each_shell():
            r = self.reset(shell, '--if-no-lease', '0', MU300_LEASES=lease)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('5e100000.usb\n', (self.gadget / 'UDC').read_text())


class Usb(ShellTest):
    """mu300-usb against a fake charger (busybox i2cget/i2cset on files) and a fake role switch."""

    def setUp(self):
        super().setUp()
        r = self.root = self.tmp / 'root'
        (r / 'run/mu300').mkdir(parents=True)
        (r / 'run/mu300/device').write_text('u30air\n')
        node = r / 'sys/firmware/devicetree/base/soc/i2c@22a0000'
        node.mkdir(parents=True)
        i2c = r / 'sys/bus/i2c/devices/i2c-6'
        i2c.mkdir(parents=True)
        (i2c / 'of_node').symlink_to(node)
        role = r / 'sys/class/usb_role/25100000.dwc3-role-switch'
        role.mkdir(parents=True)
        (role / 'role').write_text('device\n')
        self.stub('id', 'echo 0')
        self.stub('sleep', ':')
        # i2c-tools (which mu300-usb prefers to busybox's): i2cget -y BUS ADDR REG / i2cset -y BUS ADDR REG VALUE,
        # one file per register. (Not a stub called busybox: that would also replace the "busybox sh" under test.)
        # The options (-y, and -f when a kernel driver owns the address) go to $STUBLOG/flags.
        opts = 'while case $1 in -*) true ;; *) false ;; esac; do echo "$1" >> "$STUBLOG/flags"; shift; done; '
        self.stub('i2cget', opts + 'cat "$STUBLOG/reg-$3" 2>/dev/null || echo 0x00')
        self.stub('i2cset', opts + 'printf "0x%02x\\n" $(( $4 )) > "$STUBLOG/reg-$3"; '
                            'echo "$3=$(( $4 ))" >> "$STUBLOG/writes"')

    def regs(self, **values):
        for k, v in values.items():
            (self.tmp / f'reg-0x{k[1:]}').write_text(v + '\n')

    def reg(self, r):
        return int((self.tmp / f'reg-{r}').read_text(), 16)

    def usb(self, shell, *args):
        return self.script(shell, BIN / 'mu300-usb', *args, MU300_SYSROOT=self.root,
                           PATH=f'{self.stubs}:{BIN}:/usr/bin:/bin')

    def role(self):
        return (self.root / 'sys/class/usb_role/25100000.dwc3-role-switch/role').read_text().strip()

    def test_host_and_back(self):
        for shell in self.each_shell():
            self.regs(r01='0x1a', r05='0x9f', r08='0x00')   # no outside supply
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.role(), 'host')
            self.assertEqual(self.reg('0x01'), 0x1a | 0x20)    # OTG_CONFIG on, the rest untouched
            self.assertEqual(self.reg('0x05'), 0x9f & ~0x30)   # watchdog off
            r = self.usb(shell, 'device')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.role(), 'device')
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertEqual(self.reg('0x05'), (0x9f & ~0x30) | 0x10)

    def test_host_on_outside_power_leaves_the_boost_off(self):
        # power good (a hub or splitter with a charger drives VBUS): host, and not a single register written
        for shell in self.each_shell():
            (self.root / 'sys/class/usb_role/25100000.dwc3-role-switch/role').write_text('device\n')
            self.regs(r01='0x1a', r05='0x9f', r08='0x24')
            (self.tmp / 'writes').unlink(missing_ok=True)
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('outside power', r.stdout)
            self.assertFalse((self.tmp / 'writes').exists())
            self.assertEqual(self.role(), 'host')
            self.assertEqual(self.reg('0x01'), 0x1a)

    def test_boot_switches_a_leftover_boost_off(self):
        for shell in self.each_shell():
            self.regs(r01='0x3a', r05='0x8f')
            r = self.usb(shell, 'boot')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertIn('switched off', r.stdout)
            # nothing to do, nothing written
            (self.tmp / 'writes').unlink(missing_ok=True)
            self.assertEqual(self.usb(shell, 'boot').stdout, '')
            self.assertFalse((self.tmp / 'writes').exists())

    def test_under_the_kernel_driver(self):
        # bq256xx owns 6-006b (mainline with the charger patch): i2c-dev needs -f, and the watchdog stays off, or 40 s
        # later the chip would drop the driver's charge settings (input current, charge current, charging on)
        drv = self.root / 'sys/bus/i2c/devices/6-006b'
        drv.mkdir(parents=True)
        (drv / 'driver').symlink_to(self.root / 'run')
        for shell in self.each_shell():
            (self.tmp / 'flags').unlink(missing_ok=True)
            self.regs(r01='0x1a', r05='0x87', r08='0x00')
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.reg('0x01'), 0x1a | 0x20)
            r = self.usb(shell, 'device')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertEqual(self.reg('0x05'), 0x87)          # watchdog still off
            self.regs(r01='0x3a')
            r = self.usb(shell, 'boot')
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertEqual(self.reg('0x05'), 0x87)
            self.assertIn('-f', (self.tmp / 'flags').read_text().split())
        # without the driver, no -f
        (drv / 'driver').unlink()
        for shell in self.each_shell():
            (self.tmp / 'flags').unlink(missing_ok=True)
            self.usb(shell, 'boot')
            self.assertNotIn('-f', (self.tmp / 'flags').read_text().split())

    # --- USB-C power delivery (FINDINGS 150): the ump9620-tcpc module, experimental and off by default
    def pd_setup(self, bound=False):
        """A kernel with the module; modprobe binds a fake Type-C port (driver ump9620-tcpc), rmmod unbinds it."""
        r = self.root
        (r / 'lib/modules/test').mkdir(parents=True, exist_ok=True)
        (r / 'lib/modules/test/ump9620-tcpc.ko').write_text('')
        drv = r / 'sys/bus/platform/drivers/ump9620-tcpc'
        drv.mkdir(parents=True, exist_ok=True)
        self.stub('uname', 'echo test')
        port = r / 'sys/class/typec/port0'
        bind = (f'mkdir -p {port} && ln -sfn {r}/pd-dev {port}/device && mkdir -p {r}/pd-dev && '
                f'ln -sfn {drv} {r}/pd-dev/driver && '
                f'printf "host [device]\\n" > {port}/data_role && printf "source [sink]\\n" > {port}/power_role && '
                f'mkdir -p {r}/sys/module/ump9620_tcpc')
        self.stub('modprobe', f'echo "modprobe $*" >> "$STUBLOG/mod"; {bind}')
        self.stub('rmmod', f'echo "rmmod $*" >> "$STUBLOG/mod"; rm -rf {port} {r}/sys/module/ump9620_tcpc')
        (self.tmp / 'mod').unlink(missing_ok=True)
        if bound:
            subprocess.run(['sh', '-c', bind], check=True)
        return port

    def usb_pd(self, shell, *args):
        return self.script(shell, BIN / 'mu300-usb', *args, MU300_SYSROOT=self.root,
                           MU300_USB_PD_CONF=self.tmp / 'usb-pd', PATH=f'{self.stubs}:{BIN}:/usr/bin:/bin')

    def test_pd_on_loads_the_module_with_enable_and_off_unloads_it(self):
        for shell in self.each_shell():
            self.pd_setup()
            (self.tmp / 'usb-pd').unlink(missing_ok=True)
            r = self.usb_pd(shell, 'pd', 'status')
            self.assertIn('USB-C power delivery: off', r.stdout)
            r = self.usb_pd(shell, 'pd', 'on')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('EXPERIMENTAL', r.stdout)
            self.assertEqual((self.tmp / 'usb-pd').read_text(), 'on\n')
            self.assertIn('modprobe ump9620_tcpc enable=1', (self.tmp / 'mod').read_text())
            r = self.usb_pd(shell, 'pd', 'status')
            self.assertIn('data role: device', r.stdout)
            self.assertIn('power role: sink', r.stdout)
            r = self.usb_pd(shell, 'pd', 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'usb-pd').read_text(), 'off\n')
            self.assertIn('rmmod ump9620_tcpc', (self.tmp / 'mod').read_text())
            self.assertFalse((self.root / 'sys/class/typec/port0').exists())

    def test_pd_on_where_modprobe_drops_the_options(self):
        # OpenWrt's kmodloader: modprobe loads the module (and its dependencies) but without enable=1, so its probe
        # only says it is off; mu300-usb loads it again by its path with insmod, which takes the option
        for shell in self.each_shell():
            port = self.pd_setup()
            r = self.root
            ko = r / 'lib/modules/test/ump9620-tcpc.ko'
            self.stub('modprobe', f'echo "modprobe $*" >> "$STUBLOG/mod"; mkdir -p {r}/sys/module/ump9620_tcpc/parameters'
                                  f' && echo N > {r}/sys/module/ump9620_tcpc/parameters/enable')
            bind = (f'mkdir -p {port} && ln -sfn {r}/pd-dev {port}/device && mkdir -p {r}/pd-dev && '
                    f'ln -sfn {r}/sys/bus/platform/drivers/ump9620-tcpc {r}/pd-dev/driver && '
                    f'printf "host [device]\\n" > {port}/data_role && printf "source [sink]\\n" > {port}/power_role')
            self.stub('insmod', f'echo "insmod $*" >> "$STUBLOG/mod"; [ "$2" = enable=1 ] && {bind}')
            (self.tmp / 'usb-pd').unlink(missing_ok=True)
            r = self.usb_pd(shell, 'pd', 'on')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            calls = (self.tmp / 'mod').read_text().splitlines()
            self.assertEqual(calls, ['rmmod ump9620_tcpc', 'modprobe ump9620_tcpc enable=1', 'rmmod ump9620_tcpc',
                                     f'insmod {ko} enable=1'])
            self.assertIn('data role: device', self.usb_pd(shell, 'pd', 'status').stdout)
            shutil.rmtree(self.root / 'sys/class/typec')

    def test_pd_on_needs_the_module_and_the_u30_air(self):
        for shell in self.each_shell():
            self.stub('uname', 'echo test')
            shutil.rmtree(self.root / 'lib', ignore_errors=True)
            r = self.usb_pd(shell, 'pd', 'on')
            self.assertEqual(r.returncode, 1)
            self.assertIn('no ump9620-tcpc module', r.stderr)
            self.assertFalse((self.tmp / 'usb-pd').exists())
            (self.root / 'run/mu300/device').write_text('f50\n')
            r = self.usb_pd(shell, 'pd', 'on')
            self.assertEqual(r.returncode, 1)
            self.assertIn('only the U30 Air', r.stderr)
            (self.root / 'run/mu300/device').write_text('u30air\n')

    def test_boot_loads_power_delivery_only_when_it_is_on(self):
        for shell in self.each_shell():
            self.pd_setup()
            self.regs(r01='0x1a', r05='0x87')
            (self.tmp / 'usb-pd').write_text('off\n')
            self.assertEqual(self.usb_pd(shell, 'boot').returncode, 0)
            self.assertFalse((self.tmp / 'mod').exists())
            (self.tmp / 'usb-pd').write_text('on\n')
            self.regs(r01='0x3a')      # a boost left on from before the reboot: off first, then the module
            r = self.usb_pd(shell, 'boot')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.reg('0x01'), 0x1a)
            self.assertIn('modprobe ump9620_tcpc enable=1', (self.tmp / 'mod').read_text())
            # bound already: the port controller owns the boost, nothing is written, nothing loaded again
            (self.tmp / 'mod').unlink()
            (self.tmp / 'writes').unlink(missing_ok=True)
            self.regs(r01='0x3a')
            self.assertEqual(self.usb_pd(shell, 'boot').returncode, 0)
            self.assertFalse((self.tmp / 'writes').exists())
            self.assertFalse((self.tmp / 'mod').exists())
            shutil.rmtree(self.root / 'sys/class/typec')

    def test_host_and_device_ask_the_port_controller_and_write_no_charger_register(self):
        for shell in self.each_shell():
            port = self.pd_setup(bound=True)
            self.regs(r01='0x1a', r05='0x87', r08='0x00')
            (self.tmp / 'writes').unlink(missing_ok=True)
            r = self.usb_pd(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('data role swap', r.stdout)
            self.assertEqual((port / 'data_role').read_text().strip(), 'host')
            self.assertFalse((self.tmp / 'writes').exists())
            r = self.usb_pd(shell, 'status')
            self.assertIn('USB-C power delivery: on', r.stdout)
            # a partner that refuses the swap (or no PD): host on outside power only, still no register written
            (port / 'data_role').write_text('host [device]\n')
            (port / 'data_role').chmod(0o444)
            r = self.usb_pd(shell, 'host')
            self.assertEqual(r.returncode, 1)
            self.assertIn('nothing to swap roles with', r.stderr)
            self.regs(r08='0x24')
            r = self.usb_pd(shell, 'host')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('outside power', r.stdout)
            self.assertEqual(self.role(), 'host')
            self.assertFalse((self.tmp / 'writes').exists())
            # the port powers what is plugged in: device is refused (unplugging it is the way back)
            (port / 'power_role').write_text('[source] sink\n')
            r = self.usb_pd(shell, 'device')
            self.assertEqual(r.returncode, 1)
            self.assertIn('unplug it', r.stderr)
            (port / 'data_role').chmod(0o644)
            (self.root / 'sys/class/usb_role/25100000.dwc3-role-switch/role').write_text('device\n')
            shutil.rmtree(self.root / 'sys/class/typec')

    def test_f50(self):
        for shell in self.each_shell():
            (self.root / 'run/mu300/device').write_text('f50\n')
            r = self.usb(shell, 'host')
            self.assertEqual(r.returncode, 1)
            self.assertIn('only the U30 Air', r.stderr)
            self.assertEqual(self.usb(shell, 'boot').returncode, 0)   # boot is quiet on every device


@unittest.skipIf(os.name == 'nt' or os.geteuid() == 0, 'needs a user that cannot write the daemon directory')
class AtClient(ShellTest):
    """mu300-at as a user who cannot write /run/mu300-at: it says so at once (it used to loop for ever on a stale
    lock it could not remove, printing "rm: cannot remove .../lock/pid", and `mobile-data status` hung with it)."""

    def setUp(self):
        super().setUp()
        self.dir = self.tmp / 'at'
        self.dir.mkdir()
        os.mkfifo(self.dir / 'cmd')

    def tearDown(self):
        for p in (self.dir / 'lock', self.dir):
            if p.exists():
                p.chmod(0o755)
        super().tearDown()

    def at(self, shell):
        t = time.monotonic()
        r = self.script(shell, BIN / 'mu300-at', 'AT+CSQ', MU300_AT_DIR=self.dir, MU300_AT_LOCK_WAIT=20)
        return r, time.monotonic() - t

    def test_a_user_is_told_to_be_root(self):
        self.dir.chmod(0o555)
        for shell in self.each_shell():
            r, took = self.at(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('root', r.stderr)
            self.assertLess(took, 5)

    def test_a_stale_lock_it_cannot_remove(self):
        (self.dir / 'lock').mkdir()
        (self.dir / 'lock' / 'pid').write_text('999999\n')   # an owner that is gone
        (self.dir / 'lock').chmod(0o555)
        self.dir.chmod(0o555)
        for shell in self.each_shell():
            r, took = self.at(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('root', r.stderr)
            self.assertLess(took, 5)
            self.assertLess(r.stderr.count('\n'), 3, r.stderr)

    def test_a_stale_lock_it_cannot_remove_counts_as_busy(self):
        # the loop itself: a writable directory, but a lock whose dead owner's files cannot be removed. It used
        # to "continue" past the wait for ever; now it waits like for a live owner and gives up "busy".
        (self.dir / 'lock').mkdir()
        (self.dir / 'lock' / 'pid').write_text('999999\n')
        (self.dir / 'lock').chmod(0o555)
        for shell in self.each_shell():
            t = time.monotonic()
            r = self.script(shell, BIN / 'mu300-at', 'AT+CSQ', MU300_AT_DIR=self.dir, MU300_AT_LOCK_WAIT=1)
            took = time.monotonic() - t
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('busy', r.stderr)
            self.assertLess(took, 15)


class LanStart(ShellTest):
    """lan-start (bash): the bridge has udev's persistent MAC before any port joins it. Otherwise br-lan took usb0's
    MAC whenever it was up before udev got to it, its IPv6 link-local was made from that, and the address changed
    from boot to boot (seen on the U30 Air under 7.2.9)."""

    def test_udev_names_the_bridge_before_ports_join(self):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/calls"; case "$*" in "link show br-lan") exit 1;; esac; exit 0')
        self.stub('udevadm', 'echo "udevadm $*" >> "$STUBLOG/calls"')
        self.stub('dnsmasq', 'echo "dnsmasq" >> "$STUBLOG/calls"')
        r = self.script(['bash'], BIN / 'lan-start', MU300_LAN_IP='192.168.78.1')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text().splitlines()
        add = calls.index('ip link add br-lan type bridge')
        settle = next(i for i, c in enumerate(calls) if c.startswith('udevadm settle'))
        join = calls.index('ip link set usb0 master br-lan')
        up = calls.index('ip link set br-lan up')
        self.assertLess(add, settle)
        self.assertLess(settle, join)
        self.assertLess(join, up)

    def test_a_changed_address_replaces_the_old_one(self):
        """A restart with a new LAN_IP (the device manager's lan.set) takes the old address off the bridge."""
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/calls"\n'
                  'case "$*" in "-4 -o addr show dev br-lan scope global")\n'
                  '  echo "5: br-lan    inet 192.168.79.1/24 brd 192.168.79.255 scope global br-lan\\\\       valid_lft forever"\n'
                  '  echo "5: br-lan    inet 192.168.90.1/24 brd 192.168.90.255 scope global br-lan\\\\       valid_lft forever" ;;\n'
                  'esac; exit 0')
        self.stub('udevadm', 'exit 0')
        self.stub('dnsmasq', 'echo "dnsmasq" >> "$STUBLOG/calls"')
        r = self.script(['bash'], BIN / 'lan-start', MU300_LAN_IP='192.168.90.1')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text().splitlines()
        self.assertIn('ip addr del 192.168.79.1/24 dev br-lan', calls)
        self.assertNotIn('ip addr del 192.168.90.1/24 dev br-lan', calls)
        self.assertLess(calls.index('ip addr replace 192.168.90.1/24 dev br-lan'), calls.index('ip addr del 192.168.79.1/24 dev br-lan'))


class KmsgForward(ShellTest):
    """kmsg-forward: the first start of a boot forwards the ring buffer from its start (the early call traces came
    before the service and were lost with --follow-new); a restart forwards only what is new, so nothing twice."""

    def test_first_start_reads_from_the_start_restart_only_new(self):
        self.stub('dmesg', 'echo "dmesg $*" >> "$STUBLOG/calls"; echo "[    1.234567] early trace"')
        self.stub('systemd-cat', 'echo "systemd-cat $*" >> "$STUBLOG/calls"; cat >> "$STUBLOG/journal"')
        # (it runs on Ubuntu only: GNU grep's --line-buffered, which busybox's grep has not, filters the ignore list)
        import subprocess
        gnu = subprocess.run(['grep', '--line-buffered', 'x'], input='x\n', capture_output=True, text=True).returncode == 0
        for shell in self.each_shell():
            for ignore in ('', 'chatter\n') if gnu else ('',):
                mark = self.tmp / 'run' / 'kmsg-forwarded'
                (self.tmp / 'ignore').write_text(ignore)
                for f in ('calls', 'journal'):
                    (self.tmp / f).unlink(missing_ok=True)
                if mark.exists():
                    mark.unlink()
                env = dict(MU300_KMSG_MARK=mark, MU300_KMSG_IGNORE=self.tmp / 'ignore')
                for _ in range(2):
                    r = self.script(shell, BIN / 'kmsg-forward', **env)
                    self.assertEqual(r.returncode, 0, r.stderr)
                d = [c for c in (self.tmp / 'calls').read_text().splitlines() if c.startswith('dmesg')]
                self.assertEqual(len(d), 2, d)
                self.assertIn('--follow ', d[0] + ' ')
                self.assertNotIn('--follow-new', d[0])
                self.assertIn('--follow-new', d[1])
                for c in d:
                    self.assertNotIn('--notime', c)                     # the kernel's timestamps stay
                    self.assertIn('--level=emerg,alert,crit,err,warn', c)
                self.assertIn('[    1.234567] early trace', (self.tmp / 'journal').read_text())


class VpnUplink(ShellTest):
    """mu300-vpn-uplink (FINDINGS 176): at the bearer's ifup, a VPN whose current run started before it is restarted;
    one that started after it, a VPN that is off, or no run at all is left alone. mu300-vpn and logger are stubs."""

    def run_uplink(self, shell, since, enabled='1', began=None):
        run = self.tmp / 'vpnrun'
        run.mkdir(exist_ok=True)
        for f in ('since', 'engine'):
            (run / f).unlink(missing_ok=True)
        if since is not None:
            (run / 'since').write_text(f'{since}\n')
        if began is not None:
            (run / 'engine').write_text('sing-box\n')
            os.utime(run / 'engine', (began, began))
        (self.tmp / 'calls').unlink(missing_ok=True)
        self.stub('fakevpn', f'case $1 in enabled) echo {enabled} ;; *) echo "vpn $*" >> "$STUBLOG/calls" ;; esac')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/calls"')
        env = dict(MU300_VPN_CMD='fakevpn', MU300_VPN_RUN=run, MU300_UPLINK_DIR=self.tmp / 'state',
                   MU300_UPLINK_SETTLE='0', MU300_UPLINK_RUN_WAIT='0')
        r = self.script(shell, BIN / 'mu300-vpn-uplink', 'sipa_eth0', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else ''
        return r, calls

    def test_a_vpn_started_before_the_bearer_is_restarted(self):
        import time
        # the seconds in the message are counted from the script's own clock reading (state/uplink-up), which can be a
        # second or more after the test's: the expected text is taken from it, not from the test's clock
        def up():
            return int((self.tmp / 'state' / 'uplink-up').read_text())
        for shell in self.each_shell():
            since = int(time.time()) - 2
            r, calls = self.run_uplink(shell, since)
            self.assertIn('vpn restart', calls, (shell, r.stderr))
            self.assertGreaterEqual(up() - since, 2)
            self.assertIn(f'the VPN started before sipa_eth0 was up ({up() - since} s): restarting it', calls)
            # the same second counts as before
            r, calls = self.run_uplink(shell, int(time.time()))
            self.assertIn('vpn restart', calls, (shell, r.stderr))
            # a run that began before the bearer (its engine file) and reached its engine after it: restarted too
            began = int(time.time()) - 3
            r, calls = self.run_uplink(shell, began + 23, began=began)
            self.assertIn('vpn restart', calls, (shell, r.stderr))
            self.assertGreaterEqual(up() - began, 3)
            self.assertIn(f'({up() - began} s)', calls)
            self.assertTrue((self.tmp / 'state' / 'uplink-up').exists())
            self.assertFalse((self.tmp / 'state' / 'vpn-uplink.lock').exists())

    def test_left_alone_after_the_bearer_off_or_without_a_run(self):
        import time
        for shell in self.each_shell():
            now = int(time.time())
            for since, enabled, began in ((now + 30, '1', None), (now + 30, '1', now + 25), (now - 5, '0', None),
                                          (None, '1', now - 5)):
                r, calls = self.run_uplink(shell, since, enabled, began)
                self.assertNotIn('vpn restart', calls, (shell, since, enabled, began, r.stderr))

    def test_the_openwrt_hook_runs_it_for_the_bearer_only(self):
        hook = (TOP / 'openwrt/overlay/etc/hotplug.d/iface/96-mu300-vpn-uplink').read_text()
        self.assertIn('[ "$ACTION" = ifup ] || exit 0', hook)
        self.assertIn('sipa_eth*) ;;', hook)
        self.assertRegex(hook, r'/opt/mu300/bin/mu300-vpn-uplink "\$DEVICE" .*&\n')
        # and Ubuntu's dial calls it in the background once the bearer is configured
        md = (BIN / 'mobile-data').read_text()
        up = md[md.index('    echo "mobile data up on $IFACE'):]
        self.assertIn('mu300-vpn-uplink}" "$IFACE" ) </dev/null >/dev/null 2>&1 &', up[:600])


if __name__ == '__main__':
    unittest.main()


class NextBoot(ShellTest):
    """mu300-next-boot (bash) re-arms the Linux slot with tries N+1 in the slot byte of whichever slot Linux is on."""

    def setUp(self):
        super().setUp()
        import importlib.util
        spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
        self.bbi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bbi)
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        self.misc = self.tmp / 'misc'
        live = bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146')
        self.misc.write_bytes(bytes(0x800) + live + bytes(2016))
        self.blocks = self.bbi.bootloader_control(self.misc.read_bytes())   # android_a, linux_b, android_b, linux_a
        (self.run_dir / 'misc-dev').write_text(str(self.misc))
        (self.tmp / 'default-boot').write_text('linux\n')

    def nb(self, *args):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        return subprocess.run(['bash', str(BIN / 'mu300-next-boot'), *args], capture_output=True, text=True,
                              env=self.env(MU300_RUN=self.run_dir, MU300_CONF=self.tmp / 'default-boot',
                                           MU300_CMDLINE_SRC=self.tmp / 'cmdline'))

    def bc(self):
        return self.misc.read_bytes()[0x800:0x820]

    def test_slot_a(self):
        android_a, linux_b, android_b, linux_a = self.blocks
        (self.run_dir / 'linux-slot').write_text('a\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_b)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_a)
        r = self.nb('--rearm')
        self.assertEqual(r.returncode, 0, r.stderr)
        # 5 attempts by default: tries 6 in slot a's byte (12), the rest as in the trial block, CRC fixed
        want = bytearray(linux_a)
        want[12] = 0x0f | (6 << 4)
        want[28:32] = struct.pack('<I', zlib.crc32(bytes(want[:28])))
        self.assertEqual(self.bc(), bytes(want))
        self.nb('android')
        self.assertEqual(self.bc(), android_b)

    def test_lock_unlock_and_the_trial_of_a_new_image(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'linux-slot').write_text('b\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_a)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_b)
        disk = self.tmp / 'disk'
        (disk / '.mu300').mkdir(parents=True)
        def nb(*a):
            if not shutil.which('bash'):
                self.skipTest('no bash')
            return subprocess.run(['bash', str(BIN / 'mu300-next-boot'), *a], capture_output=True, text=True,
                                  env=self.env(MU300_RUN=self.run_dir, MU300_CONF=self.tmp / 'default-boot',
                                               MU300_CMDLINE_SRC=self.tmp / 'cmdline', MU300_DISK=disk))
        def want(info):
            w = bytearray(linux_b); w[14] = info
            w[28:32] = struct.pack('<I', zlib.crc32(bytes(w[:28])))
            return bytes(w)
        r = nb('lock')
        self.assertEqual(r.returncode, 0, r.stderr)
        # successful=1, prio 15, tries 6: LK never counts it down, never rolls back
        self.assertEqual(self.bc(), want(0xef))
        self.assertTrue((disk / '.mu300/boot-lock').exists())
        self.assertIn('locked: yes', nb('status').stdout)
        # a good boot keeps it locked
        nb('--rearm'); self.assertEqual(self.bc(), want(0xef))
        # mu300-update before a new boot image: the usual attempts, the lock wish kept for the next good boot
        r = nb('--trial'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bc(), want(0x6f))
        self.assertTrue((disk / '.mu300/boot-lock').exists())
        nb('--rearm'); self.assertEqual(self.bc(), want(0xef))
        # Android by hand: its block, and the wish stays for when Linux is chosen again
        nb('android'); self.assertEqual(self.bc(), android_a)
        self.assertTrue((disk / '.mu300/boot-lock').exists())
        (self.tmp / 'default-boot').write_text('linux\n')
        r = nb('unlock'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bc(), want(0x6f))
        self.assertFalse((disk / '.mu300/boot-lock').exists())
        self.assertIn('locked: no', nb('status').stdout)

    def test_slot_b_as_before(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'misc-bc-slot-a.bin').write_bytes(android_a)          # an older initramfs: old names only
        (self.run_dir / 'misc-bc-slot-b-trial.bin').write_bytes(linux_b)
        self.assertEqual(self.nb('--rearm').returncode, 0)
        self.assertEqual(self.bc()[14], 0x6f)
        self.nb('android')
        self.assertEqual(self.bc(), android_a)

    def test_without_the_file_the_booted_slot_decides(self):
        # no linux-slot (or a garbled one) but LK booted slot a: the old names are not slot a's, nothing is written
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'misc-bc-slot-a.bin').write_bytes(android_a)
        (self.run_dir / 'misc-bc-slot-b-trial.bin').write_bytes(linux_b)
        (self.tmp / 'cmdline').write_text('console=x androidboot.slot_suffix=_a\n')
        before = self.misc.read_bytes()
        for slot in (None, 'x\n'):
            if slot:
                (self.run_dir / 'linux-slot').write_text(slot)
            self.assertNotEqual(self.nb('android').returncode, 0, slot)
            (self.tmp / 'default-boot').write_text('linux\n')
            self.assertNotEqual(self.nb('--rearm').returncode, 0, slot)
            self.assertEqual(self.misc.read_bytes(), before)

    def test_slot_a_never_falls_back_to_legacy_names(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'linux-slot').write_text('a\n')
        (self.run_dir / 'misc-bc-slot-a.bin').write_bytes(android_a)
        (self.run_dir / 'misc-bc-slot-b-trial.bin').write_bytes(linux_b)
        before = self.misc.read_bytes()
        r = self.nb('android')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)

    def test_trial_with_android_as_the_default_boot_arms_nothing_and_says_so(self):
        # mu300-update calls this before it writes a boot image. With Android as the default boot there is nothing to
        # arm - every Linux boot is a one-shot trial armed from Android - and a reboot goes to Android by itself, so
        # the caller has to be able to tell (exit 3): an update that promised a reboot left a device in Android on
        # 2026-10-12, and nothing on the screen had said it would
        (self.tmp / 'default-boot').write_text('android\n')
        before = self.misc.read_bytes()
        r = self.nb('--trial')
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn('default boot is not linux', r.stderr)
        self.assertEqual(r.stdout, '')
        self.assertEqual(self.misc.read_bytes(), before)
        # --rearm is the periodic best effort (mu300-boot-ok, mu300-post): nothing to do is not a failure there
        self.assertEqual(self.nb('--rearm').returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)

    def test_an_arm_that_cannot_rebuild_the_block_is_one_boot_and_a_failure(self):
        # the initramfs' block is not the one this misc's arithmetic needs: mu300-next-boot writes that block back -
        # one boot to prove itself, not N (FINDINGS 32) - and exits 1, so mu300-update does not report the usual
        # attempts for an image that gets only the one
        linux_b = bytearray(self.blocks[1])
        linux_b[14] = 0x3f                      # tries 3: make_bc_b 2 2 cannot reproduce this block
        linux_b[28:32] = struct.pack('<I', zlib.crc32(bytes(linux_b[:28])))
        (self.run_dir / 'linux-slot').write_text('b\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(self.blocks[0])
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(bytes(linux_b))
        r = self.nb('--trial')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('arming with 1 attempt, not 5', r.stderr)
        self.assertEqual(self.bc(), bytes(linux_b))

    def slot_b_blocks(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'linux-slot').write_text('b\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_a)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_b)
        return android_a, linux_b

    def test_the_default_boot_is_written_only_after_misc_reads_back(self):
        # kanoqwq's 1f8f03fb: a switch changes misc and nothing else until misc is known good. Here: the file the
        # initramfs reads ("linux" = skip restoring Android's slot) used to be written first, whatever misc did then
        android_a, _ = self.slot_b_blocks()
        conf = self.tmp / 'default-boot'
        (self.run_dir / 'misc-dev').write_text(str(self.tmp / 'no-such-dir' / 'misc'))   # every write fails
        r = self.nb('android')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('writing misc failed', r.stderr)
        self.assertEqual(conf.read_text(), 'linux\n')
        # no Android block at all: nothing written either
        (self.run_dir / 'misc-dev').write_text(str(self.misc))
        (self.run_dir / 'misc-bc-android.bin').unlink()
        self.assertNotEqual(self.nb('android').returncode, 0)
        self.assertEqual(conf.read_text(), 'linux\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_a)
        r = self.nb('android')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.bc(), conf.read_text()), (android_a, 'android\n'))
        # and back: "linux" with a misc that cannot be written leaves "android"
        (self.run_dir / 'misc-dev').write_text(str(self.tmp / 'no-such-dir' / 'misc'))
        self.assertNotEqual(self.nb('linux').returncode, 0)
        self.assertEqual(conf.read_text(), 'android\n')
        (self.run_dir / 'misc-dev').write_text(str(self.misc))
        r = self.nb('linux')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.bc()[14], conf.read_text()), (0x6f, 'linux\n'))
        self.assertEqual([p.name for p in self.tmp.glob('.default-boot.*')], [])

    def test_the_degraded_arm_still_records_linux(self):
        # FINDINGS 32: a slot block that cannot be rebuilt here gets the initramfs' one-shot block - written and read
        # back, so "linux" is recorded with it as before (exit 1 says the arm is degraded)
        linux_b = bytearray(self.blocks[1])
        linux_b[14] = 0x3f
        linux_b[28:32] = struct.pack('<I', zlib.crc32(bytes(linux_b[:28])))
        (self.run_dir / 'linux-slot').write_text('b\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(self.blocks[0])
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(bytes(linux_b))
        (self.tmp / 'default-boot').write_text('android\n')
        r = self.nb('linux')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual((self.bc(), (self.tmp / 'default-boot').read_text()), (bytes(linux_b), 'linux\n'))

    def test_the_default_boot_is_never_written_through_a_symlink(self):
        android_a, _ = self.slot_b_blocks()
        victim = self.tmp / 'victim'
        victim.write_text('keep\n')
        conf = self.tmp / 'default-boot'
        conf.unlink()
        conf.symlink_to(victim)
        r = self.nb('android')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(victim.read_text(), 'keep\n')
        self.assertFalse(conf.is_symlink())
        self.assertEqual(conf.read_text(), 'android\n')

    def test_a_running_change_is_waited_for_and_never_overwritten(self):
        # --rearm (mu300-boot-ok) reads the default boot, then writes the Linux block: an "android" in between was
        # lost. The script holds a lock from its first read to its last write; a stub flock stands for the real one
        # here (macOS has none) and says busy while the marker exists.
        android_a, _ = self.slot_b_blocks()
        busy = self.tmp / 'busy'
        flock = self.stubs / 'flock'
        flock.write_text(f'#!/bin/sh\necho "$*" >> "{self.tmp}/flock.log"\n[ ! -e "{busy}" ]\n')
        flock.chmod(0o755)
        sleep = self.stubs / 'sleep'
        sleep.write_text('#!/bin/sh\nexit 0\n')
        sleep.chmod(0o755)
        busy.touch()
        before = self.misc.read_bytes()
        if not shutil.which('bash'):
            self.skipTest('no bash')
        env = self.env(MU300_RUN=self.run_dir, MU300_CONF=self.tmp / 'default-boot',
                       MU300_CMDLINE_SRC=self.tmp / 'cmdline', MU300_NEXT_BOOT_WAIT=3)
        run = lambda *a: subprocess.run(['bash', str(BIN / 'mu300-next-boot'), *a], capture_output=True, text=True,
                                        env=env)
        r = run('android')
        self.assertEqual(r.returncode, 1)
        self.assertIn('another mu300-next-boot is still running', r.stderr)
        self.assertEqual(self.misc.read_bytes(), before)
        self.assertEqual((self.tmp / 'default-boot').read_text(), 'linux\n')
        self.assertTrue((self.run_dir / 'next-boot.lock').exists())
        # status reads only: it never waits
        self.assertEqual(run('status').returncode, 0)
        self.assertEqual((self.tmp / 'flock.log').read_text().split('\n').count('-n 9'), 3)
        busy.unlink()
        r = run('android')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bc(), android_a)
        # a lock file that is a symlink is not opened (and nothing is serialised then, as without flock)
        (self.run_dir / 'next-boot.lock').unlink()
        (self.run_dir / 'next-boot.lock').symlink_to(self.tmp / 'elsewhere')
        busy.touch()
        self.assertEqual(run('android').returncode, 0)
        self.assertFalse((self.tmp / 'elsewhere').exists())

    @unittest.skipUnless(shutil.which('flock') and shutil.which('bash'), 'needs flock(1) and bash')
    def test_the_real_flock_holds_off_a_second_writer(self):
        import fcntl
        android_a, _ = self.slot_b_blocks()
        before = self.misc.read_bytes()
        with open(self.run_dir / 'next-boot.lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = subprocess.run(['bash', str(BIN / 'mu300-next-boot'), 'android'], capture_output=True, text=True,
                               env=self.env(MU300_RUN=self.run_dir, MU300_CONF=self.tmp / 'default-boot',
                                            MU300_CMDLINE_SRC=self.tmp / 'cmdline', MU300_NEXT_BOOT_WAIT=2))
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(self.misc.read_bytes(), before)
        r = self.nb('android')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.bc(), android_a)

    def test_without_the_slot_file_the_published_block_names_the_slot(self):
        # no linux-slot, but the initramfs' Linux block (its first bytes are the suffix LK reads): that slot, before
        # the command line and before the b of old images (kanoqwq's b8d4f222)
        android_a, linux_b, android_b, linux_a = self.blocks
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_b)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_a)
        (self.tmp / 'cmdline').write_text('console=x androidboot.slot_suffix=_b\n')
        r = self.nb('--rearm')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('slot a armed', r.stdout)
        self.assertEqual(self.bc()[12], 0x0f | (6 << 4))
        self.assertEqual(self.nb('android').returncode, 0)
        self.assertEqual(self.bc(), android_b)

    def test_blocks_that_name_another_slot_are_never_written(self):
        # the slot file says one thing, a block another: nothing is written, whatever the command (kanoqwq's b8d4f222)
        android_a, linux_b, android_b, linux_a = self.blocks
        before = self.misc.read_bytes()
        for slot, android, linux in (('a', android_b, linux_b),     # Linux's block names b
                                     ('a', android_a, linux_a),     # Android's block names a, Linux's slot
                                     ('b', android_a, linux_a),
                                     ('b', android_b, linux_b)):
            (self.run_dir / 'linux-slot').write_text(slot + '\n')
            (self.run_dir / 'misc-bc-android.bin').write_bytes(android)
            (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux)
            for cmd in (('android',), ('--rearm',), ('--trial',), ('linux',), ('lock',)):
                (self.tmp / 'default-boot').write_text('linux\n')
                r = self.nb(*cmd)
                self.assertEqual(r.returncode, 1, (slot, cmd, r.stdout))
                self.assertIn('do not match', r.stderr)
                self.assertEqual(self.misc.read_bytes(), before)
            self.assertEqual(self.nb('status').returncode, 0)          # reading is still allowed
        # a matching pair still works
        (self.run_dir / 'linux-slot').write_text('a\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_b)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_a)
        self.assertEqual(self.nb('android').returncode, 0)
        self.assertEqual(self.bc(), android_b)


class EarlyRecorder(ShellTest):
    """early-recorder's choice of boot partition: the Linux slot's, never Android's."""

    def test_slot(self):
        import re
        m = re.search(r'# --- slot begin\n(.*?)# --- slot end', (BIN / 'early-recorder').read_text(), re.S)
        self.assertIsNotNone(m, 'early-recorder has no slot block')
        run, cmdline = self.tmp / 'run', self.tmp / 'cmdline'
        run.mkdir()
        cases = [(None, None, 'b'), ('a\n', None, 'a'), ('b\n', 'androidboot.slot_suffix=_a', 'b'),
                 (None, 'x androidboot.slot_suffix=_a y', 'a'), ('junk\n', 'androidboot.slot_suffix=_a', 'a'),
                 ('junk\n', 'loglevel=5', 'b')]
        for shell in self.each_shell():
            for v, cl, want in cases:
                for p, data in ((run / 'linux-slot', v), (cmdline, cl)):
                    p.unlink(missing_ok=True)
                    if data is not None:
                        p.write_text(data)
                r = self.sh(shell, m.group(1) + '\necho "$PART"', MU300_RUN=run, MU300_CMDLINE_SRC=cmdline)
                self.assertEqual((r.stdout.strip(), r.stderr), (f'boot_{want}', ''), (v, cl))


class EarlyRecorderNode(ShellTest):
    """early-recorder writes only into the block device: on OpenWrt procd replaces /dev while it runs, and a dd in that
    moment made /dev/mmcblk0p38 a plain file in RAM that took every later write, mu300-update's boot image too (U30 Air,
    FINDINGS 86)."""

    def run_node(self, shell, logdev, sysdir):
        import re
        m = re.search(r'# --- node begin\n(.*?)# --- node end', (BIN / 'early-recorder').read_text(), re.S)
        self.assertIsNotNone(m, 'early-recorder has no node block')
        log = self.tmp / 'mknod.log'
        log.unlink(missing_ok=True)
        code = (f'mknod() {{ echo "$*" >> "{log}"; }}\nLOGDEV="{logdev}" LOGSYS="{sysdir}"\n' + m.group(1)
                + '\nif node_ok; then echo ok; else echo no; fi')
        r = self.sh(shell, code)
        return r.stdout.strip(), (log.read_text() if log.exists() else '')

    def test_node(self):
        sysdir = self.tmp / 'sys-p38'
        sysdir.mkdir()
        dev = self.tmp / 'mmcblk0p38'
        for shell in self.each_shell():
            (sysdir / 'dev').write_text('259:30\n')
            # missing: no write (hotplug makes it), nothing made here
            self.assertEqual(self.run_node(shell, dev, sysdir), ('no', ''))
            self.assertFalse(dev.exists())
            # a plain file in its place: removed, and the node made from sysfs (here mknod is a stand-in)
            dev.write_bytes(b'x' * 100)
            self.assertEqual(self.run_node(shell, dev, sysdir), ('no', f'-m 600 {dev} b 259 30\n'))
            self.assertFalse(dev.exists())
            # a link, or a sysfs entry that is not MAJOR:MINOR: left alone
            dev.symlink_to(self.tmp / 'elsewhere')
            self.assertEqual(self.run_node(shell, dev, sysdir), ('no', ''))
            self.assertTrue(dev.is_symlink())
            dev.unlink()
            dev.write_bytes(b'x')
            for bad in ('', 'junk\n', '259:\n'):
                (sysdir / 'dev').write_text(bad)
                self.assertEqual(self.run_node(shell, dev, sysdir), ('no', ''), bad)
                self.assertTrue(dev.exists(), bad)
            dev.unlink()

    def test_every_write_is_behind_node_ok(self):
        s = (BIN / 'early-recorder').read_text()
        writes = [line for line in s.splitlines() if 'of="$LOGDEV"' in line]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].strip().startswith('node_ok && dd '), writes[0])
        after = s[s.index('node_ok && dd '):]
        self.assertIn('\n    node_ok\n', after)


class ToolkitStress(ShellTest):
    """mu300-toolkit's stress test stops itself at 95 C (FINDINGS 166): on 6.18.56 the recommended 10-minute CPU test
    took the SoC to thermal-guard's 105 C power-off twice, and a hot power-off cost the next boot its eMMC."""
    def run_stress(self, shell, soc, **env):
        root = self.tmp / 'root'
        z = root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'temp').write_text(f'{soc}\n')
        self.stub('id', 'echo 0')                       # need_root
        self.stub('stress-ng', 'exec sleep 30')         # the load, without burning the test machine's CPUs
        logs = self.tmp / 'logs'
        logs.mkdir(exist_ok=True)
        t = time.monotonic()
        r = self.script(shell, BIN / 'mu300-toolkit', 'stress', 'cpu', '1', MU300_SYSROOT=root,
                        MU300_STRESS_LOGDIR=logs, TMPDIR=self.tmp, **env)
        return r, time.monotonic() - t, logs

    def test_stops_at_95_c_with_a_message(self):
        for shell in self.each_shell():
            r, took, logs = self.run_stress(shell, 96500)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(took, 20, 'stopped at the first sample, not after the minute')
            self.assertIn('STOPPED EARLY', r.stdout)
            self.assertIn('the SoC reached 96 C, the limit is 95 C', r.stdout)
            saved = list(logs.glob('mu300-stress-*.txt'))
            self.assertEqual(len(saved), 1)
            self.assertIn('STOPPED EARLY', saved[0].read_text())
            self.assertEqual(list(self.tmp.glob('mu300-stress.*')), [], 'its directory is removed')
            for f in saved:
                f.unlink()

    def test_the_limit_is_configurable(self):
        for shell in self.each_shell():
            r, took, logs = self.run_stress(shell, 91000, MU300_STRESS_LIMIT=90000)
            self.assertIn('the SoC reached 91 C, the limit is 90 C', r.stdout)
            for f in logs.glob('mu300-stress-*.txt'):
                f.unlink()


class SysupgradeWrapper(ShellTest):
    """OpenWrt's /sbin/sysupgrade here: a firmware image is refused, the configuration backup tools pass (kanoqwq's
    b8d4f222 added restore: LuCI's System > Backup "Upload archive" runs sysupgrade --restore-backup)."""
    WRAP = TOP / 'openwrt/overlay/usr/libexec/mu300-sysupgrade'

    def run_it(self, shell, *args):
        real = self.stubs / 'sysupgrade.openwrt'
        real.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$STUBLOG/real"\n')
        real.chmod(0o755)
        (self.tmp / 'real').unlink(missing_ok=True)
        r = self.script(shell, self.WRAP, *args, MU300_SYSUPGRADE=real)
        called = (self.tmp / 'real').read_text() if (self.tmp / 'real').exists() else None
        return r, called

    def test_backup_restore_and_list_pass(self):
        for shell in self.each_shell():
            for args in (['-b', '/tmp/backup.tar.gz'], ['--create-backup', '-'], ['-k', '-b', '/tmp/x.tgz'],
                         ['--restore-backup', '/tmp/backup.tar.gz'], ['-r', '/tmp/backup.tar.gz'],
                         ['-l'], ['--list-backup'], ['-u', '-c', '-l'], ['-v', '-b', 'x'], ['-q', '-r', 'x']):
                r, called = self.run_it(shell, *args)
                self.assertEqual(r.returncode, 0, (args, r.stderr))
                self.assertEqual(called, ' '.join(args) + '\n')

    def test_firmware_and_mixed_or_incomplete_requests_are_refused(self):
        for shell in self.each_shell():
            for args in ([], ['firmware.bin'], ['-n', 'firmware.bin'], ['-F', 'firmware.bin'], ['-T', 'image'],
                         ['-b'], ['-r'], ['-b', ''], ['-b', 'x', '-r', 'y'], ['-l', '-b', 'x'], ['-b', 'x', 'image'],
                         ['-l', 'image'], ['--test', 'image'], ['-r', 'x', '-n']):
                r, called = self.run_it(shell, *args)
                self.assertEqual(r.returncode, 1, args)
                self.assertIsNone(called, args)
                self.assertIn('sysupgrade is disabled', r.stderr)
